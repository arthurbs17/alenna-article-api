"""Aplica ao artigo as decisões do usuário sobre as sugestões.

Princípio para casos ambíguos: na dúvida, não altera o texto do pesquisador e
reporta o motivo. Uma sugestão problemática nunca bloqueia as demais do lote.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Iterable

from alenna.domain import (
    Action,
    ApplyResult,
    Article,
    Decision,
    Outcome,
    Segment,
    Suggestion,
    SuggestionResult,
)


@dataclass(frozen=True)
class _Edit:
    """Sugestão já ancorada no texto atual do trecho."""

    suggestion: Suggestion
    start: int
    end: int
    replacement: str

    @property
    def delta(self) -> int:
        return len(self.replacement) - (self.end - self.start)


def apply_decisions(
    article: Article,
    suggestions: Iterable[Suggestion],
    decisions: Iterable[Decision],
) -> ApplyResult:
    suggestions = tuple(suggestions)
    segments = _index_segments(article)
    results: dict[str, SuggestionResult] = {}
    errors: list[str] = []

    valid = _validate_suggestions(suggestions, segments, results)
    chosen = _resolve_decisions(decisions, suggestions, results, errors)

    accepted: dict[str, list[_Edit]] = defaultdict(list)
    pending: dict[str, list[_Edit]] = defaultdict(list)

    for suggestion in valid:
        if suggestion.id in results:  # ex.: decisões contraditórias
            continue
        decision = chosen.get(suggestion.id)
        if decision is not None and decision.action is Action.REJECT:
            results[suggestion.id] = SuggestionResult(suggestion.id, Outcome.REJECTED)
            continue

        anchor = _anchor(suggestion, segments[suggestion.segment_id].text)
        if anchor is None:
            results[suggestion.id] = SuggestionResult(
                suggestion.id,
                Outcome.STALE,
                "texto original não encontrado de forma única no trecho",
            )
            continue

        start, end = anchor
        if decision is None:
            pending[suggestion.segment_id].append(
                _Edit(suggestion, start, end, suggestion.replacement)
            )
        else:
            replacement = (
                decision.edited_replacement
                if decision.edited_replacement is not None
                else suggestion.replacement
            )
            accepted[suggestion.segment_id].append(
                _Edit(suggestion, start, end, replacement)
            )

    new_segments: list[Segment] = []
    remaining: dict[str, Suggestion] = {}
    for segment in article.segments:
        applied = _drop_conflicts(accepted[segment.id], results)
        for edit in applied:
            results[edit.suggestion.id] = SuggestionResult(edit.suggestion.id, Outcome.APPLIED)
        new_segments.append(replace(segment, text=_splice(segment.text, applied)))

        for edit in pending[segment.id]:
            rebased = _rebase(edit, applied)
            if rebased is None:
                results[edit.suggestion.id] = SuggestionResult(
                    edit.suggestion.id,
                    Outcome.SUPERSEDED,
                    "o intervalo foi alterado por uma sugestão aplicada",
                )
            else:
                results[edit.suggestion.id] = SuggestionResult(edit.suggestion.id, Outcome.PENDING)
                remaining[edit.suggestion.id] = rebased

    ordered_ids = list(dict.fromkeys(s.id for s in suggestions))
    return ApplyResult(
        article=replace(article, segments=tuple(new_segments)),
        results=tuple(results[sid] for sid in ordered_ids),
        remaining_suggestions=tuple(remaining[sid] for sid in ordered_ids if sid in remaining),
        errors=tuple(errors),
    )


def _index_segments(article: Article) -> dict[str, Segment]:
    index: dict[str, Segment] = {}
    for segment in article.segments:
        if segment.id in index:
            raise ValueError(f"trecho duplicado no artigo: {segment.id!r}")
        index[segment.id] = segment
    return index


def _validate_suggestions(
    suggestions: tuple[Suggestion, ...],
    segments: dict[str, Segment],
    results: dict[str, SuggestionResult],
) -> list[Suggestion]:
    counts: dict[str, int] = defaultdict(int)
    for suggestion in suggestions:
        counts[suggestion.id] += 1

    valid: list[Suggestion] = []
    for suggestion in suggestions:
        reason = None
        if counts[suggestion.id] > 1:
            # Não dá para saber a qual das cópias a decisão se refere.
            reason = "id de sugestão duplicado"
        elif suggestion.segment_id not in segments:
            reason = f"trecho inexistente: {suggestion.segment_id!r}"
        elif suggestion.start < 0 or suggestion.end < suggestion.start:
            reason = "intervalo inválido"
        elif len(suggestion.original) != suggestion.end - suggestion.start:
            reason = "tamanho de 'original' não bate com o intervalo"

        if reason is None:
            valid.append(suggestion)
        else:
            results[suggestion.id] = SuggestionResult(suggestion.id, Outcome.INVALID, reason)
    return valid


def _resolve_decisions(
    decisions: Iterable[Decision],
    suggestions: tuple[Suggestion, ...],
    results: dict[str, SuggestionResult],
    errors: list[str],
) -> dict[str, Decision]:
    known = {s.id for s in suggestions}
    grouped: dict[str, list[Decision]] = defaultdict(list)
    for decision in decisions:
        grouped[decision.suggestion_id].append(decision)

    chosen: dict[str, Decision] = {}
    for suggestion_id, group in grouped.items():
        if suggestion_id not in known:
            errors.append(f"decisão para sugestão inexistente: {suggestion_id!r}")
            continue
        if suggestion_id in results:  # sugestão já inválida; decisão irrelevante
            continue
        if len(set(group)) > 1:
            results[suggestion_id] = SuggestionResult(
                suggestion_id, Outcome.INVALID, "decisões contraditórias para a mesma sugestão"
            )
            continue
        chosen[suggestion_id] = group[0]
    return chosen


def _anchor(suggestion: Suggestion, text: str) -> tuple[int, int] | None:
    """Localiza a sugestão no texto atual do trecho.

    Usa os offsets se o texto ainda bate; senão, realoca só se `original`
    aparecer exatamente uma vez (ambíguo = arriscado demais para aplicar).
    """
    start, end = suggestion.start, suggestion.end
    if end <= len(text) and text[start:end] == suggestion.original:
        return start, end
    if not suggestion.original:
        # Inserção pura: não há texto para procurar.
        return None
    first = text.find(suggestion.original)
    if first == -1 or text.find(suggestion.original, first + 1) != -1:
        return None
    return first, first + len(suggestion.original)


def _overlaps(a: _Edit, b: _Edit) -> bool:
    if a.start == a.end and b.start == b.end:
        # Duas inserções no mesmo ponto: a ordem entre elas seria arbitrária.
        return a.start == b.start
    # Intervalos que apenas se encostam não conflitam; uma inserção
    # estritamente dentro de um intervalo conflita.
    return a.start < b.end and b.start < a.end


def _drop_conflicts(
    edits: list[_Edit], results: dict[str, SuggestionResult]
) -> list[_Edit]:
    """Remove (e reporta) todas as aceitas que se sobrepõem; devolve o resto ordenado."""
    conflicts: dict[str, list[str]] = defaultdict(list)
    for i, a in enumerate(edits):
        for b in edits[i + 1 :]:
            if _overlaps(a, b):
                conflicts[a.suggestion.id].append(b.suggestion.id)
                conflicts[b.suggestion.id].append(a.suggestion.id)

    for suggestion_id, others in conflicts.items():
        results[suggestion_id] = SuggestionResult(
            suggestion_id, Outcome.CONFLICT, f"sobrepõe: {', '.join(sorted(others))}"
        )
    kept = [e for e in edits if e.suggestion.id not in conflicts]
    return sorted(kept, key=lambda e: (e.start, e.end))


def _splice(text: str, edits: list[_Edit]) -> str:
    """Monta o texto novo num único passe; `edits` ordenadas e sem sobreposição.

    Todos os offsets se referem ao texto original, então a ordem de chegada
    das decisões não importa e não há deslocamento acumulado a corrigir.
    """
    parts: list[str] = []
    cursor = 0
    for edit in edits:
        parts.append(text[cursor : edit.start])
        parts.append(edit.replacement)
        cursor = edit.end
    parts.append(text[cursor:])
    return "".join(parts)


def _rebase(pending: _Edit, applied: list[_Edit]) -> Suggestion | None:
    """Ajusta os offsets de uma pendente ao texto novo; `None` se foi atropelada."""
    shift = 0
    for edit in applied:
        if _overlaps(pending, edit):
            return None
        if edit.end <= pending.start:
            shift += edit.delta
    return replace(
        pending.suggestion,
        start=pending.start + shift,
        end=pending.end + shift,
    )
