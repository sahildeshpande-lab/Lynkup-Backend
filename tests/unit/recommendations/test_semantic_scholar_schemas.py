from __future__ import annotations

from apps.recommendations.schemas import (
    SemanticScholarAuthor,
    SemanticScholarPaper,
    SemanticScholarSearchData,
)


def test_semantic_scholar_schemas_round_trip():
    data = SemanticScholarSearchData(
        total=1,
        offset=0,
        papers=[
            SemanticScholarPaper(
                paperId="p1",
                title="Example",
                authors=[SemanticScholarAuthor(authorId="a1", name="Ada")],
                year=2026,
            )
        ],
    )
    assert data.total == 1
    assert data.papers[0].paperId == "p1"
    assert data.papers[0].authors[0].name == "Ada"
