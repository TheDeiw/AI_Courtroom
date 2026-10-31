"""chat_json retry/LLMError contract with a fake provider (no network)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402
from app.schemas import TriageResult  # noqa: E402

VALID = '{"category": "boring", "interest_score": 1, "sincerity": 2, "reason": "r", "summary": "s"}'


@pytest.fixture
def fake_provider(monkeypatch):
    answers: list = []
    calls: list = []

    def fake(role, system, user, schema, temperature, attempt=0):
        calls.append(role)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(llm, "PROVIDER", "gemma_api")
    monkeypatch.setattr(llm, "_call_gemma", fake)
    return answers, calls


def test_valid_json_first_try(fake_provider):
    answers, calls = fake_provider
    answers.append(VALID)
    assert llm.chat_json("triage", "s", "u", TriageResult).category == "boring"
    assert calls == ["triage"]


def test_one_retry_after_validation_error(fake_provider):
    answers, calls = fake_provider
    answers += ['{"category": "funny"}', "```json\n" + VALID + "\n```"]
    assert llm.chat_json("triage", "s", "u", TriageResult).summary == "s"
    assert len(calls) == 2


def test_trailing_fence_after_json_is_ignored(fake_provider):
    answers, _ = fake_provider
    answers.append(VALID + "\n```")             # seen from Gemma 4 on long answers
    assert llm.chat_json("triage", "s", "u", TriageResult).category == "boring"


def test_repetition_loop_is_retried(fake_provider):
    answers, calls = fake_provider
    looped = VALID.replace('"reason": "r"', '"reason": "' + "серйозна справа і " * 12 + '"')
    answers += [looped, VALID]
    assert llm.chat_json("triage", "s", "u", TriageResult).reason == "r"
    assert len(calls) == 2


def test_find_degenerate_text():
    assert llm.find_degenerate_text({"a": ["нормальне речення про грифона та Геральта у Велені сьогодні"]}) is None
    assert llm.find_degenerate_text({"lines": [{"text": "Справа є Prämannien. " * 6}]})


def test_two_invalid_answers_raise_llm_error(fake_provider):
    answers, calls = fake_provider
    answers += ["not json", '{"interest_score": 99}']
    with pytest.raises(llm.LLMError):
        llm.chat_json("triage", "s", "u", TriageResult)
    assert len(calls) == 2


def test_provider_exception_becomes_llm_error(fake_provider):
    answers, _ = fake_provider
    answers.append(ConnectionError("network down"))
    with pytest.raises(llm.LLMError, match="network down"):
        llm.chat_json("triage", "s", "u", TriageResult)


def test_fast_role_retries_in_thinking_mode(monkeypatch):
    constrained = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            constrained.append(config.response_json_schema is not None)
            return SimpleNamespace(text=VALID if len(constrained) == 2 else "not json", candidates=[])

    monkeypatch.setattr(llm, "PROVIDER", "gemma_api")
    monkeypatch.setattr(llm, "_gemma", lambda: SimpleNamespace(models=FakeModels()))
    assert llm.chat_json("moderator", "s", "u", TriageResult).summary == "s"
    assert constrained == [True, False]       # fast schema-constrained try, then the thinking mode


def test_prefilter_uses_fast_model(monkeypatch):
    monkeypatch.setattr(llm, "PROVIDER", "ollama")
    assert llm.model_for("prefilter") == llm.FAST
    assert llm.model_for("judge") == llm.MAIN
