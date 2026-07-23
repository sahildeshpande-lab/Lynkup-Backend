from __future__ import annotations

from pydantic import BaseModel, Field


class SemanticScholarAuthor(BaseModel):
    authorId: str | None = None
    name: str | None = None


class SemanticScholarPaper(BaseModel):
    paperId: str
    title: str | None = None
    authors: list[SemanticScholarAuthor] = Field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    url: str | None = None
    citationCount: int | None = None


class SemanticScholarSearchData(BaseModel):
    total: int = 0
    offset: int = 0
    next: int | None = None
    papers: list[SemanticScholarPaper] = Field(default_factory=list)
