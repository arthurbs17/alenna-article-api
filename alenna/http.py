"""Camada HTTP fina: só converte JSON <-> domínio. Toda a regra está em `engine.py`."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from alenna import domain
from alenna.config import get_settings
from alenna.engine import apply_decisions

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="Improve Article API",
    description="Aplica ao artigo as sugestões de tradução/revisão aceitas pelo usuário.",
    version="0.1.0",
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["POST"],
        allow_headers=["*"],
    )


class SegmentIn(BaseModel):
    id: str
    text: str


class ArticleIn(BaseModel):
    id: str
    segments: list[SegmentIn]


class SuggestionIn(BaseModel):
    id: str
    segment_id: str
    start: int
    end: int
    original: str
    replacement: str


class DecisionIn(BaseModel):
    suggestion_id: str
    action: domain.Action
    edited_replacement: str | None = None


class ApplyRequest(BaseModel):
    article: ArticleIn
    suggestions: list[SuggestionIn] = Field(default_factory=list)
    decisions: list[DecisionIn] = Field(default_factory=list)


class SuggestionResultOut(BaseModel):
    suggestion_id: str
    outcome: domain.Outcome
    reason: str | None = None


class ApplyResponse(BaseModel):
    article: ArticleIn
    results: list[SuggestionResultOut]
    remaining_suggestions: list[SuggestionIn]
    errors: list[str]


@app.post("/articles/apply-suggestions", response_model=ApplyResponse)
def apply_suggestions(request: ApplyRequest) -> ApplyResponse:
    article = domain.Article(
        request.article.id,
        tuple(domain.Segment(s.id, s.text) for s in request.article.segments),
    )
    suggestions = [domain.Suggestion(**s.model_dump()) for s in request.suggestions]
    decisions = [domain.Decision(**d.model_dump()) for d in request.decisions]

    try:
        result = apply_decisions(article, suggestions, decisions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ApplyResponse(
        article=ArticleIn(
            id=result.article.id,
            segments=[SegmentIn(id=s.id, text=s.text) for s in result.article.segments],
        ),
        results=[SuggestionResultOut(**vars(r)) for r in result.results],
        remaining_suggestions=[SuggestionIn(**vars(s)) for s in result.remaining_suggestions],
        errors=list(result.errors),
    )
