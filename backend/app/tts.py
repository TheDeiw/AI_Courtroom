"""Court voice-over with Kokoro-82M (kokoro-onnx).

generate_dialogue_audio(dialogue, case_id) renders every line into
frontend/static/audio/cases/{case_id}/NN_<speaker>.mp3 and returns the lines as dicts with an
extra "audio_url" field (None when a line could not be rendered — court.html then falls back to
browser speechSynthesis). TTS never raises into the court pipeline.

Kokoro has no Ukrainian voice, so we phonemize Ukrainian with espeak-ng ourselves and feed the
phonemes to Kokoro; voices only give timbre. Model files (~350 MB, gitignored):
    python -m app.tts --download     # fetch kokoro-v1.0.onnx + voices-v1.0.bin
    python -m app.tts                # smoke test: one line per role → .../cases/smoke/
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
MODEL_DIR = Path(os.getenv("KOKORO_DIR", BACKEND_DIR / "models" / "kokoro"))
MODEL_PATH = Path(os.getenv("KOKORO_MODEL", MODEL_DIR / "kokoro-v1.0.onnx"))
VOICES_PATH = Path(os.getenv("KOKORO_VOICES", MODEL_DIR / "voices-v1.0.bin"))
MODEL_URLS = {
    MODEL_PATH: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    VOICES_PATH: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}

AUDIO_DIR = REPO_DIR / "frontend" / "static" / "audio" / "cases"
AUDIO_URL_PREFIX = "/static/audio/cases"     # frontend/ is served from "/"
TTS_ENABLED = os.getenv("TTS_ENABLED", "1").lower() not in ("0", "false", "no", "off")
TTS_LANG = os.getenv("TTS_LANG", "uk")        # espeak-ng language for phonemization
TTS_FORMAT = os.getenv("TTS_FORMAT", "mp3")   # mp3 (needs ffmpeg) | wav

# Voice presets by role. speed mirrors the speechSynthesis rates from ARCHITECTURE.md 10.4.
# Picked by intelligibility of Ukrainian (Whisper-small character error rate on 3 test phrases):
# em_alex 5%, ef_dora 6%, af_bella 7%, am_michael 8% … bm_george 11%, am_fenrir 12%.
VOICE_PRESETS: dict[str, dict[str, Any]] = {
    "judge":      {"voice": "em_alex",    "speed": 0.9},
    "prosecutor": {"voice": "am_michael", "speed": 1.05},
    "defense":    {"voice": "ef_dora",    "speed": 1.0},
    "clerk":      {"voice": "af_bella",   "speed": 1.1},
}
EMOTION_SPEED = {"neutral": 1.0, "angry": 1.08, "ironic": 0.97, "solemn": 0.92}

_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NOT_SPEAKABLE = re.compile(r"[^\w\s.,!?;:'\"()\-—–…%]")

_kokoro = None
_espeak = None
_init_lock = threading.Lock()
_synth_lock = threading.Lock()   # one TTS job at a time: Kokoro already uses all CPU cores


class TTSUnavailable(RuntimeError):
    pass


def _engine():
    """Lazy-load Kokoro and an espeak-ng backend that drops (en)/(uk) language-switch flags."""
    global _kokoro, _espeak
    with _init_lock:
        if _kokoro is None:
            missing = [p for p in (MODEL_PATH, VOICES_PATH) if not p.exists()]
            if missing:
                raise TTSUnavailable(f"Kokoro model files missing: {missing}. Run: python -m app.tts --download")
            from kokoro_onnx import Kokoro
            from phonemizer.backend import EspeakBackend

            kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))  # also points phonemizer at espeak-ng
            quiet = logging.getLogger("phonemizer.quiet")
            quiet.setLevel(logging.ERROR)
            _espeak = EspeakBackend(TTS_LANG, preserve_punctuation=True, with_stress=True,
                                    language_switch="remove-flags", logger=quiet)
            _kokoro = kokoro
        return _kokoro, _espeak


def normalize_text(text: str) -> str:
    """Make a dialogue line speakable: no emoji, nicknames without '_', № → 'номер'."""
    t = text.replace("№", " номер ").replace("«", '"').replace("»", '"')
    t = t.replace("’", "'").replace("ʼ", "'").replace("_", " ")
    t = _NOT_SPEAKABLE.sub(" ", t)
    return " ".join(t.split())


def phonemize(text: str) -> str:
    kokoro, espeak = _engine()
    from kokoro_onnx.tokenizer import _espeak_lock  # espeak-ng has process-global state

    with _espeak_lock:
        ph = espeak.phonemize([normalize_text(text)], strip=True)[0]
    return " ".join(kokoro.tokenizer.known(ph).split())


def synthesize(text: str, speaker: str, emotion: str = "neutral") -> tuple[np.ndarray, int]:
    kokoro, _ = _engine()
    preset = VOICE_PRESETS.get(speaker, VOICE_PRESETS["clerk"])
    speed = preset["speed"] * EMOTION_SPEED.get(emotion, 1.0)
    phonemes = phonemize(text)
    if not phonemes:
        raise ValueError(f"nothing to say in {text!r}")
    return kokoro.create(phonemes, voice=preset["voice"], speed=speed, is_phonemes=True)


def _write_audio(samples: np.ndarray, sr: int, path_no_ext: Path) -> Path:
    if TTS_FORMAT == "mp3" and shutil.which("ffmpeg"):
        out = path_no_ext.with_suffix(".mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
             "-c:a", "libmp3lame", "-q:a", "4", str(out)],
            input=np.asarray(samples, dtype=np.float32).tobytes(), check=True, timeout=60,
        )
        return out
    out = path_no_ext.with_suffix(".wav")
    sf.write(out, samples, sr)
    return out


def _as_dicts(dialogue: Any) -> list[dict]:
    lines = getattr(dialogue, "lines", dialogue)   # Dialogue model or a list
    return [ln.model_dump() if hasattr(ln, "model_dump") else dict(ln) for ln in lines]


def generate_dialogue_audio(dialogue: Any, case_id: int | str) -> list[dict]:
    """Render each line to audio; return line dicts with "audio_url" (None on failure).

    dialogue: Dialogue | list[DialogueLine] | list[dict]. Never raises.
    """
    lines = _as_dicts(dialogue)
    for ln in lines:
        ln["audio_url"] = None
    if not TTS_ENABLED or not lines:
        return lines
    case_key = str(case_id)
    if not _SAFE_CASE_ID.match(case_key):
        log.error("tts: unsafe case_id %r, skipping audio", case_id)
        return lines

    with _synth_lock:
        try:
            _engine()
        except Exception as e:
            log.warning("tts disabled for case %s: %s", case_key, e)
            return lines
        out_dir = AUDIO_DIR / case_key
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in list(out_dir.glob("*.mp3")) + list(out_dir.glob("*.wav")):  # regenerated case
            old.unlink()
        version = int(time.time())  # cache-buster: file names repeat after «Перегенерувати»
        t0, total_audio = time.monotonic(), 0.0
        for i, ln in enumerate(lines, 1):
            speaker = ln.get("speaker", "clerk")
            try:
                samples, sr = synthesize(ln.get("text", ""), speaker, ln.get("emotion", "neutral"))
                path = _write_audio(samples, sr, out_dir / f"{i:02d}_{speaker}")
                ln["audio_url"] = f"{AUDIO_URL_PREFIX}/{case_key}/{path.name}?v={version}"
                total_audio += len(samples) / sr
            except Exception as e:
                log.warning("tts: case %s line %d failed: %s", case_key, i, e)
        log.info("tts: case %s — %d lines, %.1fs audio in %.1fs", case_key,
                 sum(1 for ln in lines if ln["audio_url"]), total_audio, time.monotonic() - t0)
    return lines


def download_models() -> None:
    for path, url in MODEL_URLS.items():
        if path.exists():
            print(f"ok      {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"get     {url}")
        tmp = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(path)
        print(f"saved   {path} ({path.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if "--download" in sys.argv:
        download_models()
        sys.exit(0)
    demo = [
        {"speaker": "clerk", "emotion": "neutral",
         "text": "Слухається справа №7: «Чат проти kotyk_2007». Обвинувачений забанений за образу стримера."},
        {"speaker": "prosecutor", "emotion": "angry",
         "text": "Ваша честь, повідомлення написане з комами і без жодної помилки. Коти так не пишуть!"},
        {"speaker": "defense", "emotion": "ironic",
         "text": "Протестую! Мій підзахисний готовий показати фото кота Барсика. 🐱"},
        {"speaker": "judge", "emotion": "solemn",
         "text": "Вирок: розбанити з останнім попередженням. Барсику — заборона наближатися до клавіатури."},
    ]
    for line in generate_dialogue_audio(demo, "smoke"):
        print(f"{line['speaker']:<10} {line['audio_url']}")
