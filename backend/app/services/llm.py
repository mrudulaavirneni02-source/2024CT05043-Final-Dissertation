import asyncio

import httpx
from threading import Event

from app.core.cancellation import raise_if_cancelled
from app.core.config import Settings
from app.core.errors import AppError
from app.models.schemas import RetrievedChunk
from app.services.text_processing import tokenize


MAX_CHUNK_PROMPT_CHARS = 2_200


SYSTEM_PROMPT = """You are a retrieval-augmented research assistant.

Answer only from the supplied excerpts. Do not use background knowledge, make inferences beyond the excerpts, or suggest
possible answers. Every factual sentence must end with one or more excerpt citations such as [1] or [2]. If the excerpts
do not directly answer the question, respond exactly: "Not found in the indexed documents.""" 


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        temperature: float,
        *,
        cancel_event: Event | None = None,
    ) -> str:
        if not chunks:
            return "I do not have enough indexed context to answer. Ingest the seed abstract or upload documents first."
        raise_if_cancelled(cancel_event)

        context = "\n\n".join(
            (
                f"[{index}] Source: {chunk.source}, Page: {chunk.metadata.get('page_number', 'unknown')}, "
                f"Section: {chunk.metadata.get('section', 'unknown')}, Score: {chunk.score:.3f}\n{_relevant_excerpt(chunk.text, question)}"
            )
            for index, chunk in enumerate(chunks, start=1)
        )
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            "Use the context below as the only source of truth. Give at most three concise sentences. "
            "Do not explain what a source might contain.\n\n"
            f"Context:\n{context}\n\nQuestion: {question}\n\nAnswer:"
        )
        payload = {
            "model": self.settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": self.settings.ollama_num_ctx,
                "num_predict": self.settings.ollama_num_predict,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
                request_task = asyncio.create_task(client.post(f"{self.settings.ollama_base_url.rstrip('/')}/api/generate", json=payload))
                if cancel_event is None:
                    response = await request_task
                else:
                    try:
                        while not request_task.done():
                            raise_if_cancelled(cancel_event)
                            try:
                                response = await asyncio.wait_for(asyncio.shield(request_task), timeout=0.2)
                            except TimeoutError:
                                continue
                            break
                        else:
                            response = request_task.result()
                    except Exception:
                        request_task.cancel()
                        await asyncio.gather(request_task, return_exceptions=True)
                        raise
                response.raise_for_status()
        except Exception as exc:
            import traceback

            traceback.print_exc()

            raise AppError(
                f"{type(exc).__name__}: {exc}",
                status_code=503,
                code="ollama_unavailable",
            ) from exc
        data = response.json()
        return str(data.get("response", "")).strip() or "Ollama returned an empty response."


def _relevant_excerpt(text: str, question: str) -> str:
    """Select query-relevant passages so large chunks fit within the local model context."""
    text = str(text).strip()
    if len(text) <= MAX_CHUNK_PROMPT_CHARS:
        return text

    query_terms = {term for term in tokenize(question) if len(term) > 2}
    words = text.split()
    window_size = 150
    step = 100
    windows = []
    for start in range(0, len(words), step):
        window = words[start : start + window_size]
        if not window:
            break
        overlap = len(query_terms.intersection(tokenize(" ".join(window))))
        windows.append((overlap, start, " ".join(window)))
        if start + window_size >= len(words):
            break

    selected = sorted(sorted(windows, key=lambda item: (-item[0], item[1]))[:2], key=lambda item: item[1])
    excerpt = "\n… [excerpt] …\n".join(window for _, _, window in selected).strip()
    if len(excerpt) > MAX_CHUNK_PROMPT_CHARS:
        return excerpt[:MAX_CHUNK_PROMPT_CHARS].rsplit(" ", 1)[0] + " …"
    return excerpt
