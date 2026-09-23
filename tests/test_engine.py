import random

import pytest

from alenna.domain import Action, Article, Decision, Outcome, Segment, Suggestion
from alenna.engine import apply_decisions


def article(*texts: str) -> Article:
    return Article("a1", tuple(Segment(f"s{i}", t) for i, t in enumerate(texts, start=1)))


def sug(sid: str, text: str, original: str, replacement: str, segment_id: str = "s1", nth: int = 0) -> Suggestion:
    """Cria uma sugestão apontando para a `nth` ocorrência de `original` em `text`."""
    start = -1
    for _ in range(nth + 1):
        start = text.index(original, start + 1)
    return Suggestion(sid, segment_id, start, start + len(original), original, replacement)


def insertion(sid: str, at: int, replacement: str, segment_id: str = "s1") -> Suggestion:
    return Suggestion(sid, segment_id, at, at, "", replacement)


def accept(sid: str, edited: str | None = None) -> Decision:
    return Decision(sid, Action.ACCEPT, edited)


def reject(sid: str) -> Decision:
    return Decision(sid, Action.REJECT)


TEXT = "The resuts shows that the metod works."


# --- Básico -------------------------------------------------------------------


def test_no_decisions_keeps_text_and_everything_pending():
    art = article(TEXT)
    s = [sug("x", TEXT, "resuts", "results"), sug("y", TEXT, "metod", "method")]
    result = apply_decisions(art, s, [])

    assert result.article == art
    assert [r.outcome for r in result.results] == [Outcome.PENDING, Outcome.PENDING]
    assert result.remaining_suggestions == tuple(s)


def test_accept_one():
    result = apply_decisions(article(TEXT), [sug("x", TEXT, "resuts", "results")], [accept("x")])
    assert result.article.text == "The results shows that the metod works."
    assert result.outcome_of("x") is Outcome.APPLIED
    assert result.remaining_suggestions == ()


def test_reject_one():
    result = apply_decisions(article(TEXT), [sug("x", TEXT, "resuts", "results")], [reject("x")])
    assert result.article.text == TEXT
    assert result.outcome_of("x") is Outcome.REJECTED


def test_mixed_decisions_in_same_segment():
    s = [
        sug("a", TEXT, "resuts", "results"),
        sug("b", TEXT, "shows", "show"),
        sug("c", TEXT, "metod", "method"),
    ]
    result = apply_decisions(article(TEXT), s, [accept("a"), reject("b"), accept("c")])
    assert result.article.text == "The results shows that the method works."


def test_result_does_not_depend_on_order_of_suggestions_or_decisions():
    s = [
        sug("a", TEXT, "resuts", "results"),
        sug("b", TEXT, "shows", "show"),
        sug("c", TEXT, "metod", "method"),
    ]
    d = [accept("a"), accept("b"), accept("c")]
    expected = "The results show that the method works."
    for perm in ([0, 1, 2], [2, 1, 0], [1, 2, 0]):
        result = apply_decisions(article(TEXT), [s[i] for i in perm], [d[i] for i in reversed(perm)])
        assert result.article.text == expected


def test_length_changing_edits_deletion_insertion_expansion():
    text = "very very good result"
    s = [
        Suggestion("del", "s1", 0, 5, "very ", ""),  # remove o primeiro "very "
        insertion("ins", len(text), "s"),  # insere no fim
        sug("exp", text, "good", "remarkably good"),
    ]
    result = apply_decisions(article(text), s, [accept("del"), accept("ins"), accept("exp")])
    assert result.article.text == "very remarkably good results"


def test_segments_are_independent():
    art = article("Frist paragraph.\n", "Second paragrap.")
    s = [
        sug("a", "Frist paragraph.\n", "Frist", "First", "s1"),
        sug("b", "Second paragrap.", "paragrap", "paragraph", "s2"),
    ]
    result = apply_decisions(art, s, [accept("a"), accept("b")])
    assert [seg.text for seg in result.article.segments] == ["First paragraph.\n", "Second paragraph."]
    assert result.article.text == "First paragraph.\nSecond paragraph."


def test_input_is_not_mutated():
    art = article(TEXT)
    s = [sug("x", TEXT, "resuts", "results")]
    apply_decisions(art, s, [accept("x")])
    assert art.text == TEXT
    assert s[0].replacement == "results"


def test_no_op_suggestion():
    result = apply_decisions(article(TEXT), [sug("x", TEXT, "works", "works")], [accept("x")])
    assert result.article.text == TEXT
    assert result.outcome_of("x") is Outcome.APPLIED


# --- Conflitos ----------------------------------------------------------------


def test_partial_overlap_is_conflict_and_other_edits_still_apply():
    s = [
        sug("a", TEXT, "The resuts", "Our results"),
        sug("b", TEXT, "resuts shows", "results show"),
        sug("c", TEXT, "metod", "method"),
    ]
    result = apply_decisions(article(TEXT), s, [accept("a"), accept("b"), accept("c")])
    assert result.article.text == "The resuts shows that the method works."
    assert result.outcome_of("a") is Outcome.CONFLICT
    assert result.outcome_of("b") is Outcome.CONFLICT
    assert result.outcome_of("c") is Outcome.APPLIED
    assert "b" in result.results[0].reason


def test_whole_segment_translation_conflicts_with_point_revision():
    text = "O método funciona."
    s = [
        Suggestion("tr", "s1", 0, len(text), text, "The method works."),
        sug("rv", text, "funciona", "funcionou"),
    ]
    result = apply_decisions(article(text), s, [accept("tr"), accept("rv")])
    assert result.article.text == text
    assert result.outcome_of("tr") is Outcome.CONFLICT
    assert result.outcome_of("rv") is Outcome.CONFLICT


def test_whole_segment_translation_alone_applies():
    text = "O método funciona."
    s = [Suggestion("tr", "s1", 0, len(text), text, "The method works.")]
    result = apply_decisions(article(text), s, [accept("tr")])
    assert result.article.text == "The method works."


def test_touching_ranges_are_not_conflicts():
    text = "abcdef"
    s = [Suggestion("a", "s1", 0, 3, "abc", "X"), Suggestion("b", "s1", 3, 6, "def", "Y")]
    result = apply_decisions(article(text), s, [accept("a"), accept("b")])
    assert result.article.text == "XY"


def test_insertion_at_range_boundary_applies_in_position_order():
    text = "abcdef"
    s = [
        Suggestion("r", "s1", 2, 4, "cd", "CD"),
        insertion("before", 2, "["),
        insertion("after", 4, "]"),
    ]
    result = apply_decisions(article(text), s, [accept("r"), accept("before"), accept("after")])
    assert result.article.text == "ab[CD]ef"


def test_insertion_inside_range_is_conflict():
    text = "abcdef"
    s = [Suggestion("r", "s1", 1, 5, "bcde", "Z"), insertion("i", 3, "!")]
    result = apply_decisions(article(text), s, [accept("r"), accept("i")])
    assert result.article.text == text
    assert {r.outcome for r in result.results} == {Outcome.CONFLICT}


def test_two_insertions_at_same_point_are_conflict():
    s = [insertion("i1", 3, "X"), insertion("i2", 3, "Y")]
    result = apply_decisions(article("abcdef"), s, [accept("i1"), accept("i2")])
    assert result.article.text == "abcdef"
    assert {r.outcome for r in result.results} == {Outcome.CONFLICT}


def test_rejected_overlapping_suggestion_does_not_cause_conflict():
    s = [sug("a", TEXT, "resuts", "results"), sug("b", TEXT, "resuts shows", "results show")]
    result = apply_decisions(article(TEXT), s, [accept("a"), reject("b")])
    assert result.article.text == "The results shows that the metod works."


def test_conflicting_suggestions_return_rebased_and_can_be_resolved_next_round():
    # "o teste" aparece 3 vezes: sem o rebase, a realocação não salvaria a sugestão.
    text = "o teste. o teste. o teste"
    s = [
        Suggestion("early", "s1", 0, 1, "o", "O nosso"),  # aplicada, desloca o resto (+6)
        Suggestion("x", "s1", 9, 16, "o teste", "o ensaio"),
        Suggestion("y", "s1", 11, 16, "teste", "exame"),
    ]
    first = apply_decisions(article(text), s, [accept("early"), accept("x"), accept("y")])
    assert first.outcome_of("x") is Outcome.CONFLICT
    remaining = {r.id: r for r in first.remaining_suggestions}
    assert set(remaining) == {"x", "y"}
    new_text = first.article.segments[0].text
    assert new_text[remaining["x"].start : remaining["x"].end] == "o teste"
    assert remaining["x"].start == 15

    second = apply_decisions(first.article, first.remaining_suggestions, [accept("x"), reject("y")])
    assert second.outcome_of("x") is Outcome.APPLIED
    assert second.article.text == "O nosso teste. o ensaio. o teste"


def test_conflicting_suggestion_returns_with_its_own_replacement_not_the_user_edit():
    s = [sug("a", TEXT, "The resuts", "Our results"), sug("b", TEXT, "resuts shows", "results show")]
    result = apply_decisions(article(TEXT), s, [accept("a", "My results"), accept("b")])
    assert result.remaining_suggestions == tuple(s)


# --- Texto desatualizado ------------------------------------------------------


def test_stale_offsets_are_relocated_when_original_is_unique():
    # A sugestão foi gerada antes de o usuário inserir "Surprisingly, " no início.
    s = [sug("x", TEXT, "metod", "method")]
    edited = "Surprisingly, " + TEXT
    result = apply_decisions(article(edited), s, [accept("x")])
    assert result.article.text == "Surprisingly, The resuts shows that the method works."
    assert result.outcome_of("x") is Outcome.APPLIED


def test_original_missing_is_stale():
    s = [sug("x", TEXT, "metod", "method")]
    result = apply_decisions(article("The results show that the approach works."), s, [accept("x")])
    assert result.outcome_of("x") is Outcome.STALE
    assert result.article.text == "The results show that the approach works."


def test_ambiguous_original_is_stale():
    text = "the cat and the dog"
    s = [Suggestion("x", "s1", 0, 3, "the", "The")]
    moved = "and " + text  # offsets não batem mais e "the" aparece duas vezes
    result = apply_decisions(article(moved), s, [accept("x")])
    assert result.outcome_of("x") is Outcome.STALE
    assert result.article.text == moved


def test_ambiguous_overlapping_occurrences_are_stale():
    s = [Suggestion("x", "s1", 5, 7, "aa", "b")]
    result = apply_decisions(article("aaa"), s, [accept("x")])
    assert result.outcome_of("x") is Outcome.STALE


def test_offsets_beyond_shrunk_segment_are_relocated_or_stale():
    s = [Suggestion("x", "s1", 30, 35, "metod", "method")]
    assert apply_decisions(article("metod ok"), s, [accept("x")]).article.text == "method ok"
    assert apply_decisions(article("ok"), s, [accept("x")]).outcome_of("x") is Outcome.STALE


def test_insertion_with_out_of_range_offset_is_stale():
    result = apply_decisions(article("abc"), [insertion("i", 10, "!")], [accept("i")])
    assert result.outcome_of("i") is Outcome.STALE


def test_resending_already_applied_suggestion_is_stale():
    s = [sug("x", TEXT, "metod", "method")]
    first = apply_decisions(article(TEXT), s, [accept("x")])
    second = apply_decisions(first.article, s, [accept("x")])
    assert second.outcome_of("x") is Outcome.STALE
    assert second.article == first.article


# --- Rebase das pendentes -----------------------------------------------------


def test_pending_is_rebased_and_applies_correctly_in_next_round():
    s = [sug("a", TEXT, "resuts", "results"), sug("b", TEXT, "metod", "method")]
    first = apply_decisions(article(TEXT), s, [accept("a")])
    (b,) = first.remaining_suggestions
    assert b.start == s[1].start + 1  # "resuts" -> "results" (+1 caractere)
    assert first.article.segments[0].text[b.start : b.end] == "metod"

    second = apply_decisions(first.article, first.remaining_suggestions, [accept("b")])
    assert second.article.text == "The results shows that the method works."


def test_pending_before_applied_edit_keeps_offsets():
    s = [sug("a", TEXT, "resuts", "results"), sug("b", TEXT, "metod", "method")]
    result = apply_decisions(article(TEXT), s, [accept("b")])
    assert result.remaining_suggestions == (s[0],)


def test_pending_overlapping_applied_edit_is_superseded():
    s = [sug("a", TEXT, "resuts shows", "results show"), sug("b", TEXT, "shows", "show")]
    result = apply_decisions(article(TEXT), s, [accept("a")])
    assert result.outcome_of("b") is Outcome.SUPERSEDED
    assert result.remaining_suggestions == ()


def test_pending_relocated_suggestion_gets_fresh_offsets():
    s = [sug("x", TEXT, "metod", "method")]
    edited = "Hi. " + TEXT
    result = apply_decisions(article(edited), s, [])
    (x,) = result.remaining_suggestions
    assert edited[x.start : x.end] == "metod"


@pytest.mark.parametrize("seed", range(50))
def test_property_sequential_rounds_equal_single_batch(seed):
    """Aceitar tudo de uma vez == aceitar em várias rodadas usando as pendentes rebaseadas."""
    rng = random.Random(seed)
    text = "".join(rng.choice("abcde ") for _ in range(60))

    # Gera intervalos não sobrepostos (inserções só em pontos distintos).
    cuts = sorted(rng.sample(range(len(text) + 1), 16))
    suggestions = []
    for i in range(0, len(cuts), 2):
        start, end = cuts[i], cuts[i + 1]
        if rng.random() < 0.3:
            end = start
        replacement = "".join(rng.choice("XYZ") for _ in range(rng.randint(0, 6)))
        suggestions.append(Suggestion(f"s{i}", "s1", start, end, text[start:end], replacement))

    batch = apply_decisions(article(text), suggestions, [accept(s.id) for s in suggestions])
    assert all(r.outcome is Outcome.APPLIED for r in batch.results)

    current, remaining = article(text), list(suggestions)
    rng.shuffle(remaining)
    while remaining:
        k = rng.randint(1, len(remaining))
        chosen = {s.id for s in remaining[:k]}
        step = apply_decisions(current, remaining, [accept(sid) for sid in chosen])
        current, remaining = step.article, list(step.remaining_suggestions)

    assert current.text == batch.article.text


# --- Entrada malformada -------------------------------------------------------


def test_decision_for_unknown_suggestion_goes_to_errors_and_rest_applies():
    s = [sug("x", TEXT, "metod", "method")]
    result = apply_decisions(article(TEXT), s, [accept("ghost"), accept("x")])
    assert result.article.text == "The resuts shows that the method works."
    assert len(result.errors) == 1 and "ghost" in result.errors[0]


def test_identical_duplicate_decisions_are_idempotent():
    s = [sug("x", TEXT, "metod", "method")]
    result = apply_decisions(article(TEXT), s, [accept("x"), accept("x")])
    assert result.outcome_of("x") is Outcome.APPLIED


def test_contradictory_decisions_are_invalid():
    s = [sug("x", TEXT, "metod", "method")]
    result = apply_decisions(article(TEXT), s, [accept("x"), reject("x")])
    assert result.outcome_of("x") is Outcome.INVALID
    assert result.article.text == TEXT


def test_accept_with_different_edits_is_contradictory():
    s = [sug("x", TEXT, "metod", "method")]
    result = apply_decisions(article(TEXT), s, [accept("x", "approach"), accept("x")])
    assert result.outcome_of("x") is Outcome.INVALID


def test_suggestion_for_unknown_segment_is_invalid():
    s = [Suggestion("x", "nope", 0, 3, "The", "A")]
    result = apply_decisions(article(TEXT), s, [accept("x")])
    assert result.outcome_of("x") is Outcome.INVALID
    assert result.errors == ()


def test_duplicate_suggestion_ids_are_invalid():
    s = [sug("x", TEXT, "metod", "method"), sug("x", TEXT, "resuts", "results")]
    result = apply_decisions(article(TEXT), s, [accept("x")])
    assert result.article.text == TEXT
    assert result.results == (result.results[0],)
    assert result.outcome_of("x") is Outcome.INVALID


@pytest.mark.parametrize(
    "start,end,original",
    [(-1, 2, "abc"), (5, 3, ""), (0, 3, "ab")],
    ids=["negative", "inverted", "length-mismatch"],
)
def test_malformed_ranges_are_invalid(start, end, original):
    result = apply_decisions(article("abcdef"), [Suggestion("x", "s1", start, end, original, "Z")], [accept("x")])
    assert result.outcome_of("x") is Outcome.INVALID


def test_duplicate_segment_ids_raise():
    art = Article("a1", (Segment("s1", "a"), Segment("s1", "b")))
    with pytest.raises(ValueError):
        apply_decisions(art, [], [])


# --- Outros -------------------------------------------------------------------


def test_accept_with_user_edit():
    s = [sug("x", TEXT, "metod", "method")]
    result = apply_decisions(article(TEXT), s, [accept("x", "approach")])
    assert result.article.text == "The resuts shows that the approach works."


def test_accept_with_empty_edit_deletes():
    text = "a very good result"
    s = [sug("x", text, "very ", "really ")]
    result = apply_decisions(article(text), s, [accept("x", "")])
    assert result.article.text == "a good result"


def test_unicode_offsets_are_code_points():
    text = "Análise 🧪 do métdo"
    s = [sug("x", text, "métdo", "método")]
    assert s[0].start == 13  # o emoji conta como 1 (em UTF-16 seriam 2)
    result = apply_decisions(article(text), s, [accept("x")])
    assert result.article.text == "Análise 🧪 do método"


def test_insertion_into_empty_segment():
    result = apply_decisions(article(""), [insertion("i", 0, "Abstract")], [accept("i")])
    assert result.article.text == "Abstract"


def test_empty_inputs():
    result = apply_decisions(Article("a1", ()), [], [])
    assert result.article.text == "" and result.results == () and result.errors == ()
