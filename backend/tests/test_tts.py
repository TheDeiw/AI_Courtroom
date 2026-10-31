"""TTS text cleanup and fail-safe behaviour (no model loading)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import tts  # noqa: E402
from app.schemas import Dialogue, DialogueLine  # noqa: E402


def test_normalize_text():
    out = tts.normalize_text("Справа №7: «Чат проти kotyk_2007» 🐱😂 хом’як")
    assert "номер 7" in out
    assert "kotyk 2007" in out
    assert "🐱" not in out and "«" not in out
    assert "хом'як" in out


def test_audio_failure_never_raises(monkeypatch):
    def broken():
        raise tts.TTSUnavailable("no model")

    monkeypatch.setattr(tts, "_engine", broken)
    monkeypatch.setattr(tts, "TTS_ENABLED", True)
    d = Dialogue(lines=[DialogueLine(speaker="judge", emotion="solemn", text="Вирок: розбанити.")])
    lines = tts.generate_dialogue_audio(d, 1)
    assert lines == [{"speaker": "judge", "emotion": "solemn", "text": "Вирок: розбанити.", "audio_url": None}]


def test_unsafe_case_id_is_rejected(monkeypatch):
    monkeypatch.setattr(tts, "TTS_ENABLED", True)
    lines = tts.generate_dialogue_audio([{"speaker": "clerk", "emotion": "neutral", "text": "Тест"}], "../../etc")
    assert lines[0]["audio_url"] is None
