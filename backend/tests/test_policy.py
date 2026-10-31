import pytest

from app import policy


def decision(**kw):
    # ModerationDecision — контракт B (schemas.py); поки файлу немає, ці тести пропускаються
    schemas = pytest.importorskip("app.schemas")
    base = dict(decision="timeout", rule="insult", duration_min=10, confidence=0.9,
                reason="Образа глядача", user_summary="Раніше порушень не було")
    return schemas.ModerationDecision(**{**base, **kw})


def test_low_confidence_is_acquit():
    d = policy.guard_moderation(decision(decision="ban", confidence=0.4))
    assert d.decision == "acquit"
    assert d.duration_min == 0


def test_unsure_ban_becomes_10_min_timeout():
    d = policy.guard_moderation(decision(decision="ban", confidence=0.7))
    assert d.decision == "timeout"
    assert d.duration_min == 10


def test_confident_ban_stays_ban():
    d = policy.guard_moderation(decision(decision="ban", confidence=0.85))
    assert d.decision == "ban"


def test_timeout_is_capped_at_60():
    d = policy.guard_moderation(decision(decision="timeout", duration_min=500))
    assert d.decision == "timeout"
    assert d.duration_min == 60


def test_zero_timeout_becomes_5():
    d = policy.guard_moderation(decision(decision="timeout", duration_min=0))
    assert d.duration_min == 5


def test_rule_none_with_ban_is_acquit():
    d = policy.guard_moderation(decision(decision="ban", rule="none", confidence=0.95))
    assert d.decision == "acquit"


def test_interesting_hate_goes_to_support():
    assert policy.guard_triage("interesting", "hate") == "disputed"


@pytest.mark.parametrize("rule", ["threat", "doxxing", "sexual"])
def test_interesting_severe_goes_to_support(rule):
    assert policy.guard_triage("interesting", rule) == "disputed"


def test_interesting_spoiler_stays_interesting():
    assert policy.guard_triage("interesting", "spoiler") == "interesting"


@pytest.mark.parametrize("category", ["boring", "disputed"])
def test_other_categories_unchanged(category):
    assert policy.guard_triage(category, "hate") == category
