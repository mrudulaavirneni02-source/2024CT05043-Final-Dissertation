from statistics import mean
from time import perf_counter
from math import isfinite

from loguru import logger

from app.models.schemas import EvaluationItem, EvaluationResponse, RetrievalMode
from app.services.llm import OllamaClient, _relevant_excerpt
from app.services.retrieval import RetrievalService

try:
    import ragas
    from langchain_ollama import ChatOllama
    from ragas.dataset_schema import SingleTurnSample
    from ragas.llms import LangchainLLMWrapper
    # Ragas 0.4's collection metrics require an Instructor-compatible client;
    # the compatibility metric supports the LangChain Ollama wrapper used here.
    from ragas.metrics._context_precision import ContextPrecision
    from ragas.metrics._context_recall import ContextRecall
    from ragas.metrics._factual_correctness import FactualCorrectness
    from ragas.metrics._faithfulness import Faithfulness
except ImportError:
    ragas = None
    ChatOllama = None
    SingleTurnSample = None
    LangchainLLMWrapper = None
    ContextPrecision = None
    ContextRecall = None
    FactualCorrectness = None
    Faithfulness = None

RAGAS_AVAILABLE = all(
    module is not None
    for module in (ChatOllama, SingleTurnSample, LangchainLLMWrapper, Faithfulness, ContextPrecision, ContextRecall, FactualCorrectness)
)
RAGAS_VERSION = getattr(ragas, "__version__", None) if RAGAS_AVAILABLE else None
RAGAS_CONTEXT_MAX_CHARS = 1_800
RAGAS_CONTEXT_LIMIT = 3


class EvaluationService:
    def __init__(self, retrieval: RetrievalService) -> None:
        self.retrieval = retrieval

    def is_available(self) -> bool:
        return RAGAS_AVAILABLE

    async def run(
        self,
        questions: list[str],
        mode: RetrievalMode,
        relevant_sources: dict[str, list[str]] | None = None,
        reference_answers: dict[str, str] | None = None,
        settings=None,
        include_ragas: bool = True,
        source: str | None = None,
    ) -> EvaluationResponse:
        items: list[EvaluationItem] = []
        for question in questions:
            started = perf_counter()
            results = self.retrieval.search(question, mode=mode, source=source) if source else self.retrieval.search(question, mode=mode)
            latency_ms = (perf_counter() - started) * 1000
            top_score = results[0].score if results else 0.0
            expected_sources = (relevant_sources or {}).get(question, [])
            reference_answer = (reference_answers or {}).get(question, "").strip()
            relevance = _relevance_flags(results, expected_sources)
            relevant_found = sum(1 for value in relevance if value)
            # Retrieval metrics are ground-truth metrics, not similarity-score
            # metrics.  Without a relevant source they are reported as zero
            # rather than incorrectly awarding a perfect score.
            precision = relevant_found / len(results) if results and expected_sources else 0.0
            recall = relevant_found / len(expected_sources) if expected_sources else 0.0
            mrr = _mrr(relevance) if expected_sources else 0.0
            answer = ""
            faithfulness: float | None = None
            answer_correctness: float | None = None
            context_precision: float | None = None
            context_recall: float | None = None
            ragas_error: str | None = None
            if settings is not None:
                answer = await OllamaClient(settings).generate(question, results, temperature=0.2)
                if include_ragas:
                    try:
                        scores, errors = await _ragas_scores(question, answer, results, reference_answer, settings)
                        faithfulness = scores["faithfulness"]
                        answer_correctness = scores["answer_correctness"]
                        context_precision = scores["context_precision"]
                        context_recall = scores["context_recall"]
                        ragas_error = "; ".join(errors.values()) or None
                    except RuntimeError as exc:
                        ragas_error = str(exc)
                        logger.warning("RAGAS faithfulness skipped for evaluation question: {}", exc)
            items.append(
                EvaluationItem(
                    question=question,
                    retrieved=len(results),
                    top_score=top_score,
                    average_similarity=round(mean([result.score for result in results]), 4) if results else 0.0,
                    context_available=bool(expected_sources and relevant_found),
                    precision_at_k=round(precision, 4),
                    recall_at_k=round(min(recall, 1.0), 4),
                    mrr=round(mrr, 4),
                    hit_rate=1.0 if relevant_found else 0.0,
                    retrieval_latency_ms=round(latency_ms, 2),
                    faithfulness=faithfulness,
                    answer_correctness=answer_correctness,
                    context_precision=context_precision,
                    context_recall=context_recall,
                    ragas_error=ragas_error,
                )
            )
        average_precision = mean([item.precision_at_k for item in items]) if items else 0.0
        average_recall = mean([item.recall_at_k for item in items]) if items else 0.0
        average_mrr = mean([item.mrr for item in items]) if items else 0.0
        scored_faithfulness = [item.faithfulness for item in items if item.faithfulness is not None]
        scored_answer_correctness = [item.answer_correctness for item in items if item.answer_correctness is not None]
        scored_context_precision = [item.context_precision for item in items if item.context_precision is not None]
        scored_context_recall = [item.context_recall for item in items if item.context_recall is not None]
        ragas_errors = [item.ragas_error for item in items if item.ragas_error]
        return EvaluationResponse(
            items=items,
            average_top_score=round(mean([item.top_score for item in items]), 4) if items else 0.0,
            average_precision=round(average_precision, 4),
            average_recall=round(average_recall, 4),
            average_mrr=round(average_mrr, 4),
            hit_rate=round(mean([item.hit_rate for item in items]), 4) if items else 0.0,
            average_latency_ms=round(mean([item.retrieval_latency_ms for item in items]), 2) if items else 0.0,
            average_faithfulness=round(mean(scored_faithfulness), 4) if scored_faithfulness else None,
            average_answer_correctness=round(mean(scored_answer_correctness), 4) if scored_answer_correctness else None,
            average_context_precision=round(mean(scored_context_precision), 4) if scored_context_precision else None,
            average_context_recall=round(mean(scored_context_recall), 4) if scored_context_recall else None,
            ragas_completed=include_ragas and len(scored_faithfulness) == len(items),
            ragas_error=ragas_errors[0] if ragas_errors else None,
        )


async def _ragas_scores(question: str, answer: str, results, reference_answer: str, settings) -> tuple[dict[str, float | None], dict[str, str]]:
    """Run available Ragas metrics; reference-based scores need a reference answer."""
    if not RAGAS_AVAILABLE:
        raise RuntimeError("RAGAS dependencies are not available.")
    # Judge exactly the evidence made available to the answering model, not an
    # arbitrary beginning/end truncation of a different chunk set.
    contexts = [_compact_context(_relevant_excerpt(result.text, question)) for result in results[:RAGAS_CONTEXT_LIMIT]]
    scores: dict[str, float | None] = {
        "faithfulness": None,
        "answer_correctness": None,
        "context_precision": None,
        "context_recall": None,
    }
    errors: dict[str, str] = {}
    if not answer or not contexts:
        return scores, errors

    judge = ChatOllama(
        model=settings.ragas_ollama_model or settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        format="json",
        num_ctx=8192,
        num_predict=min(settings.ollama_num_predict, 256),
    )
    wrapper = LangchainLLMWrapper(judge)
    sample = SingleTurnSample(user_input=question, response=answer, retrieved_contexts=contexts, reference=reference_answer or None)
    metrics = {"faithfulness": Faithfulness(llm=wrapper)}
    if reference_answer:
        metrics.update(
            {
                "answer_correctness": FactualCorrectness(llm=wrapper),
                "context_precision": ContextPrecision(llm=wrapper),
                "context_recall": ContextRecall(llm=wrapper),
            }
        )
    for name, metric in metrics.items():
        try:
            value = await metric.single_turn_ascore(sample, timeout=settings.ollama_timeout_seconds)
            numeric_value = float(value)
            if not isfinite(numeric_value):
                raise ValueError("RAGAS returned a non-numeric score")
            scores[name] = round(numeric_value, 4)
        except Exception as exc:
            logger.warning("RAGAS {} failed: {}", name, exc)
            errors[name] = _friendly_metric_error(name, exc)
    return scores, errors


def _friendly_metric_error(name: str, exc: Exception) -> str:
    label = name.replace("_", " ").capitalize()
    message = str(exc)
    if "Failed to parse" in message or "validation error" in message:
        return f"{label} received invalid JSON from the local judge model. Try again with the correct source selected."
    if isinstance(exc, TimeoutError) or "timeout" in message.lower():
        return f"{label} timed out while the local judge model was scoring."
    return f"{label} could not be scored: {message[:180] or type(exc).__name__}"


def _compact_context(text: str) -> str:
    """Keep the local LLM judge prompt small while retaining both ends of a large chunk."""
    text = str(text).strip()
    if len(text) <= RAGAS_CONTEXT_MAX_CHARS:
        return text
    tail_size = 300
    head_size = RAGAS_CONTEXT_MAX_CHARS - tail_size
    return f"{text[:head_size]}\n… [chunk shortened for evaluation] …\n{text[-tail_size:]}"


def _relevance_flags(results, expected_sources: list[str]) -> list[bool]:
    if expected_sources:
        expected = {source.lower() for source in expected_sources}
        flags = []
        for result in results:
            source = result.source.lower()
            filename = str(result.metadata.get("filename", "")).lower()
            flags.append(any(item in source or item in filename for item in expected))
        return flags
    return [False for _ in results]


def _mrr(flags: list[bool]) -> float:
    for index, is_relevant in enumerate(flags, start=1):
        if is_relevant:
            return 1 / index
    return 0.0
