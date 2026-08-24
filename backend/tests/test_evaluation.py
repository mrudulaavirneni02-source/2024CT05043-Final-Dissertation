import pytest

from app.models.schemas import RetrievedChunk
from app.services.evaluation import EvaluationService, _friendly_metric_error


class DummySettings:
    min_context_score = 0.08
    ollama_model = "llama3.1:8b"


class DummyRetrieval:
    def __init__(self):
        self.settings = DummySettings()

    def search(self, query, mode="hybrid", top_k=None, cancel_event=None):
        return [
            RetrievedChunk(
                id="1",
                text="Retrieval grounding is essential for enterprise RAG systems.",
                source="doc.pdf",
                score=0.08,
                metadata={"page_number": 1, "section": "Results"},
            )
        ]

    def confidence(self, results, query=None):
        return 0.2


@pytest.mark.asyncio
async def test_evaluation_run_returns_ragas_metrics(monkeypatch):
    async def fake_generate(self, question, chunks, temperature, *, cancel_event=None):
        return "Grounding is essential for enterprise RAG systems."

    monkeypatch.setattr("app.services.evaluation.OllamaClient.generate", fake_generate)

    async def fake_ragas_scores(question, answer, results, reference_answer, settings):
        assert settings.ollama_model == "llama3.1:8b"
        assert answer
        assert results
        return (
            {
                "faithfulness": 1.0,
                "answer_correctness": 0.8,
                "context_precision": 0.75,
                "context_recall": 0.9,
            },
            {},
        )

    monkeypatch.setattr("app.services.evaluation._ragas_scores", fake_ragas_scores)

    service = EvaluationService(DummyRetrieval())
    response = await service.run(
        ["What is grounding?"],
        "hybrid",
        reference_answers={"What is grounding?": "Grounding is essential for enterprise RAG systems."},
        settings=DummySettings(),
    )

    assert len(response.items) == 1
    assert response.items[0].faithfulness == 1.0
    assert response.average_faithfulness == 1.0
    assert response.average_answer_correctness == 0.8
    assert response.average_context_precision == 0.75
    assert response.average_context_recall == 0.9
    assert response.ragas_completed


@pytest.mark.asyncio
async def test_evaluation_keeps_retrieval_metrics_when_ragas_times_out(monkeypatch):
    async def fake_generate(self, question, chunks, temperature, *, cancel_event=None):
        return "Grounding is essential for enterprise RAG systems."

    async def timeout_ragas_scores(question, answer, results, reference_answer, settings):
        raise RuntimeError("RAGAS faithfulness computation failed: timed out")

    monkeypatch.setattr("app.services.evaluation.OllamaClient.generate", fake_generate)
    monkeypatch.setattr("app.services.evaluation._ragas_scores", timeout_ragas_scores)

    response = await EvaluationService(DummyRetrieval()).run(
        ["What is grounding?"],
        "hybrid",
        relevant_sources={"What is grounding?": ["doc.pdf"]},
        settings=DummySettings(),
    )

    assert response.average_precision > 0
    assert response.average_faithfulness is None
    assert not response.ragas_completed
    assert response.ragas_error == "RAGAS faithfulness computation failed: timed out"


def test_ragas_parse_errors_are_presented_as_actionable_messages():
    message = _friendly_metric_error("faithfulness", ValueError("Failed to parse StatementGeneratorOutput from completion"))

    assert message == "Faithfulness received invalid JSON from the local judge model. Try again with the correct source selected."
