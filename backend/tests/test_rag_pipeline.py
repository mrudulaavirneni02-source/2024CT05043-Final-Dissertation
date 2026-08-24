from app.api.routes import should_ground_context
from app.models.schemas import RetrievedChunk
from app.services.llm import _relevant_excerpt
from app.services.retrieval import RetrievalService


def test_confidence_increases_with_relevant_overlap():
    service = object.__new__(RetrievalService)
    results = [
        RetrievedChunk(
            id="1",
            text="Retrieval grounding improves confidence for enterprise RAG answers.",
            source="doc.pdf",
            score=0.0086,
            metadata={"page_number": 1, "section": "Results"},
        ),
        RetrievedChunk(
            id="2",
            text="This chunk also discusses retrieval confidence and answer grounding.",
            source="doc.pdf",
            score=0.0068,
            metadata={"page_number": 2, "section": "Methods"},
        ),
        RetrievedChunk(
            id="3",
            text="The system should avoid hallucinations by citing retrieved context.",
            source="doc.pdf",
            score=0.0047,
            metadata={"page_number": 3, "section": "Discussion"},
        ),
    ]

    confidence = service.confidence(results, query="retrieval grounding confidence")

    assert confidence > 0.05
    assert confidence > results[0].score


def test_grounding_allows_generation_when_question_subject_is_covered():
    results = [
        RetrievedChunk(
            id="1",
            text="The enterprise document describes how retrieval confidence is evaluated.",
            source="doc.pdf",
            score=0.0086,
            metadata={"page_number": 1, "section": "Results"},
        ),
        RetrievedChunk(
            id="2",
            text="The answer should be grounded in retrieved evidence and cite source sections.",
            source="doc.pdf",
            score=0.0065,
            metadata={"page_number": 2, "section": "Methods"},
        ),
    ]

    assert should_ground_context(0.8, results, query="retrieval confidence", min_context_score=0.15)


def test_grounding_blocks_generic_overlap_without_question_subject():
    results = [
        RetrievedChunk(
            id="1",
            text="This study reports students' learning performance in a questionnaire.",
            source="unrelated.pdf",
            score=1.0,
            metadata={},
        )
    ]

    assert not should_ground_context(
        0.8,
        results,
        query="What contradiction did the Spanish verb conjugation study find?",
        min_context_score=0.15,
    )


def test_llm_context_uses_query_relevant_excerpt_for_large_chunks():
    text = ("unrelated filler " * 600) + "Spanish interleaving practice produced better verb-conjugation performance. " + ("more filler " * 600)

    excerpt = _relevant_excerpt(text, "What Spanish practice improved verb-conjugation performance?")

    assert len(excerpt) <= 2200
    assert "interleaving practice" in excerpt
