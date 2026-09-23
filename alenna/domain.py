"""Modelos de domínio. Todos imutáveis: a lógica nunca altera a entrada."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Action(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


class Outcome(str, Enum):
    APPLIED = "applied"  # aceita e aplicada ao texto
    REJECTED = "rejected"  # rejeitada pelo usuário
    PENDING = "pending"  # sem decisão; continua válida (offsets rebaseados)
    CONFLICT = "conflict"  # aceita, mas sobrepõe outra aceita; nenhuma das duas aplicada
    STALE = "stale"  # o texto mudou e não foi possível ancorar a sugestão com segurança
    SUPERSEDED = "superseded"  # pendente cujo trecho foi alterado por outra sugestão aplicada
    INVALID = "invalid"  # entrada malformada (trecho inexistente, id duplicado, etc.)


@dataclass(frozen=True)
class Segment:
    id: str
    text: str


@dataclass(frozen=True)
class Article:
    id: str
    segments: tuple[Segment, ...]

    @property
    def text(self) -> str:
        # Cada trecho carrega seus próprios separadores (espaços, quebras de linha).
        return "".join(segment.text for segment in self.segments)


@dataclass(frozen=True)
class Suggestion:
    """Troca `original` (em `[start, end)` do texto do trecho) por `replacement`.

    - revisão pontual: intervalo cobre uma palavra/frase;
    - tradução do trecho: intervalo cobre o trecho inteiro;
    - inserção: `start == end` e `original == ""`;
    - remoção: `replacement == ""`.
    """

    id: str
    segment_id: str
    start: int
    end: int
    original: str
    replacement: str


@dataclass(frozen=True)
class Decision:
    suggestion_id: str
    action: Action
    # "Aceitar com ajuste": o usuário editou o texto sugerido antes de aceitar.
    # `None` = usa o replacement da sugestão; `""` = apagar o intervalo.
    edited_replacement: str | None = None


@dataclass(frozen=True)
class SuggestionResult:
    suggestion_id: str
    outcome: Outcome
    reason: str | None = None


@dataclass(frozen=True)
class ApplyResult:
    article: Article
    # Um resultado por id de sugestão, na ordem em que as sugestões chegaram.
    results: tuple[SuggestionResult, ...]
    # Sugestões ainda decidíveis (pendentes e em conflito), com offsets ajustados para o texto novo.
    remaining_suggestions: tuple[Suggestion, ...]
    # Problemas que não pertencem a nenhuma sugestão (ex.: decisão para id inexistente).
    errors: tuple[str, ...] = field(default_factory=tuple)

    def outcome_of(self, suggestion_id: str) -> Outcome:
        for result in self.results:
            if result.suggestion_id == suggestion_id:
                return result.outcome
        raise KeyError(suggestion_id)
