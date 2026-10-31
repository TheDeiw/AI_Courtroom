"""One interface for every agent: chat_json() and embed() (ARCHITECTURE.md, 8.1).

Providers (LLM_PROVIDER):
  gemma_api — Gemma 4 via Google AI Studio (google-genai SDK), JSON output against the Pydantic
              JSON Schema: schema-constrained for fast roles, schema-in-prompt for the rest (see below);
  ollama    — local Gemma 3 via Ollama, format=<json schema>, num_ctx=8192;
  anthropic — cloud fallback from section 4.2 (needs `pip install anthropic` + ANTHROPIC_API_KEY).

embed() always uses local Ollama bge-m3 (1024 dims): precedents in pgvector must share one space.

Every failure (provider error, network, blocked answer, invalid JSON twice) is raised as LLMError,
so callers can apply the safe defaults from section 9.5 with a single `except LLMError`.
"""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import httpx
import ollama
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # backend/.env; real env vars win

# Some shells export SSL_CERT_FILE="" — httpx then fails every TLS handshake. Drop empty values.
for _var in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
    if os.environ.get(_var) == "":
        os.environ.pop(_var)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for _noisy in ("httpx", "httpcore", "google_genai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger(__name__)

PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
FAST = os.getenv("FAST_MODEL", "gemma3:4b")          # Ollama tags
MAIN = os.getenv("MAIN_MODEL", "gemma3:12b")
EMBED = os.getenv("EMBED_MODEL", "bge-m3")
# Google AI Studio serves Gemma 4 only (Gemma 3 returns 404). 26b-a4b is MoE with ~4B active
# params: ~2-5 s per call. gemma-4-31b-it is stronger but ~30 s per call and flakier (HTTP 500s).
GEMMA_FAST = os.getenv("GEMMA_FAST_MODEL", "gemma-4-26b-a4b-it")
GEMMA_MAIN = os.getenv("GEMMA_MAIN_MODEL", "gemma-4-26b-a4b-it")
# Two Gemma decoding modes (both answer application/json and are validated by Pydantic):
#  - schema-constrained (response_json_schema): ~3 s, but longer Ukrainian text often comes out
#    garbled or looping — fine for the short, latency-critical prefilter/moderator answers;
#  - JSON mode with the schema in the prompt: the model thinks first (~1-2k hidden tokens,
#    ~25-35 s) and writes clean Ukrainian — used for triage and the court, which run in background.
GEMMA_SCHEMA_ROLES = {r.strip() for r in os.getenv("GEMMA_SCHEMA_ROLES", "prefilter,moderator").split(",")}
NUM_CTX = 8192
# Caps runaway generation (repetition loops): without it a call can hang ~2 min and end in HTTP 504.
# Normal answers are 100-600 tokens; the dialogue (8-12 lines in Ukrainian) needs ~1500.
MAX_OUTPUT_TOKENS = {"dialogue": 3072}
DEFAULT_MAX_OUTPUT_TOKENS = 1024
THINKING_MAX_OUTPUT_TOKENS = 8192            # includes the hidden thinking tokens
GEMMA_TIMEOUT_S = float(os.getenv("GEMMA_TIMEOUT_S", "60"))
GEMMA_THINKING_TIMEOUT_S = float(os.getenv("GEMMA_THINKING_TIMEOUT_S", "150"))
# Gemma 4 26b sometimes loops inside a string ("серйозна серйозна серйозна…") while the JSON stays
# valid. A text field whose word 3-grams repeat more than this share counts as an invalid answer.
MAX_REPEAT_SHARE = 0.3
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}


class LLMError(Exception):
    pass


def model_for(role: str) -> str:
    fast = role == "prefilter"
    if PROVIDER == "gemma_api":
        return GEMMA_FAST if fast else GEMMA_MAIN
    if PROVIDER == "anthropic":
        return "claude-haiku-5-5" if fast else "claude-opus-5-5"
    return FAST if fast else MAIN


def chat_json(role: str, system: str, user: str, schema: type[BaseModel], temperature: float = 0.2) -> BaseModel:
    if PROVIDER == "anthropic":
        return _chat_json_anthropic(role, system, user, schema)
    call = _call_gemma if PROVIDER == "gemma_api" else _call_ollama
    last_error: Exception | None = None
    for attempt in range(2):  # одна повторна спроба, якщо JSON не пройшов валідацію
        # The retry runs cooler: a hot second sample of a long answer tends to degenerate.
        temp = temperature if attempt == 0 else max(0.1, temperature - 0.3)
        t0 = time.monotonic()
        try:
            raw = call(role, system, user, schema, temp)
        except LLMError:
            raise
        except Exception as e:  # network, provider, SDK errors → one error type for callers
            raise LLMError(f"{role}: {type(e).__name__}: {e}") from e
        try:
            result = schema.model_validate_json(_extract_json(raw))
        except ValidationError as e:
            last_error = e
            log.warning("%s: invalid JSON (attempt %d): %s | raw=%.300r", role, attempt + 1, e, raw)
            continue
        looped = find_degenerate_text(result.model_dump())
        if looped:
            last_error = ValueError(f"repetition loop in answer: {looped!r}")
            log.warning("%s: %s (attempt %d)", role, last_error, attempt + 1)
            continue
        log.info("%s via %s/%s: ok in %.1fs", role, PROVIDER, model_for(role), time.monotonic() - t0)
        return result
    raise LLMError(f"{role}: invalid JSON: {last_error}")


def find_degenerate_text(value) -> str | None:
    """First string (in nested dicts/lists) that loops on itself, or None."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return next((s for s in map(find_degenerate_text, value) if s), None)
    if isinstance(value, str):
        words = re.findall(r"\w+", value.lower())
        grams = [tuple(words[i:i + 3]) for i in range(len(words) - 2)]
        if len(grams) >= 8 and 1 - len(set(grams)) / len(grams) > MAX_REPEAT_SHARE:
            return value[:80]
    return None


def embed(text: str) -> list[float]:
    try:
        return list(ollama.embed(model=EMBED, input=text).embeddings[0])
    except Exception as e:
        raise LLMError(f"embed: {type(e).__name__}: {e}") from e


def _max_tokens(role: str) -> int:
    return MAX_OUTPUT_TOKENS.get(role, DEFAULT_MAX_OUTPUT_TOKENS)


def _extract_json(raw: str) -> str:
    """The JSON object from a model answer: drops ``` fences and stray text around it."""
    s = (raw or "").strip()
    start, end = s.find("{"), s.rfind("}")
    return s[start:end + 1] if 0 <= start < end else s


# --- ollama ----------------------------------------------------------------

def _call_ollama(role, system, user, schema, temperature) -> str:
    r = ollama.chat(
        model=model_for(role),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        format=schema.model_json_schema(),
        options={"temperature": temperature, "num_ctx": NUM_CTX, "num_predict": _max_tokens(role)},
    )
    return r.message.content


# --- gemma_api (Google AI Studio) ------------------------------------------

_gemma_client = None
_gemma_lock = threading.Lock()


def _gemma():
    global _gemma_client
    with _gemma_lock:
        if _gemma_client is None:
            from google import genai
            from google.genai import types

            key = os.getenv("GEMMA_API_KEY")
            if not key:
                raise LLMError("GEMMA_API_KEY is not set (backend/.env)")
            _gemma_client = genai.Client(
                api_key=key, http_options=types.HttpOptions(timeout=int(GEMMA_TIMEOUT_S * 1000)))
        return _gemma_client


def _call_gemma(role, system, user, schema, temperature) -> str:
    from google.genai import errors, types

    # A moderation pipeline has to read toxic chat, so input blocking is off; the output is a
    # structured decision and the prompts forbid repeating offensive words.
    safety = [types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE) for c in (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )]
    if "all" in GEMMA_SCHEMA_ROLES or role in GEMMA_SCHEMA_ROLES:
        mode = dict(system_instruction=system, response_json_schema=schema.model_json_schema(),
                    max_output_tokens=_max_tokens(role),
                    http_options=types.HttpOptions(timeout=int(GEMMA_TIMEOUT_S * 1000)))
    else:
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        mode = dict(system_instruction=f"{system}\nReturn ONLY a JSON object that matches this JSON Schema:\n"
                                       f"{schema_text}",
                    max_output_tokens=THINKING_MAX_OUTPUT_TOKENS,
                    http_options=types.HttpOptions(timeout=int(GEMMA_THINKING_TIMEOUT_S * 1000)))
    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        safety_settings=safety,
        **mode,
    )
    for attempt in range(2):  # one quick retry for transient HTTP errors (429/5xx) and timeouts
        try:
            r = _gemma().models.generate_content(model=model_for(role), contents=user, config=config)
        except (errors.APIError, httpx.TimeoutException) as e:
            code = getattr(e, "code", "timeout")
            if attempt == 0 and (code == "timeout" or code in TRANSIENT_HTTP):
                log.warning("%s: Gemma API %s, retrying", role, code)
                time.sleep(2)
                continue
            raise
        if not r.text:
            # e.g. FinishReason.RECITATION: return "" so chat_json's validation retry resamples it
            reason = r.candidates[0].finish_reason if r.candidates else r.prompt_feedback
            log.warning("%s: empty answer from Gemma API (%s)", role, reason)
            return ""
        return r.text
    raise LLMError(f"{role}: Gemma API unavailable")


# --- anthropic (cloud fallback, section 4.2) -------------------------------

_anthropic_client = None


def _chat_json_anthropic(role, system, user, schema):
    global _anthropic_client
    try:
        import anthropic

        if _anthropic_client is None:
            _anthropic_client = anthropic.Anthropic()
        # temperature не передаємо: Claude API його більше не приймає
        extra = {} if role == "prefilter" else {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        r = _anthropic_client.beta.messages.parse(
            model=model_for(role), max_tokens=16000, system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema, **extra,
        )
    except Exception as e:
        raise LLMError(f"{role}: {type(e).__name__}: {e}") from e
    if r.stop_reason == "refusal" or r.parsed_output is None:
        raise LLMError(f"{role}: {r.stop_reason}")
    return r.parsed_output


if __name__ == "__main__":  # smoke test: python -m app.llm
    from app.schemas import TriageResult

    print(f"provider={PROVIDER} main={model_for('triage')} fast={model_for('prefilter')} embed={EMBED}")
    print(chat_json("triage", "Classify the appeal. Answer in Ukrainian.", "розбаньте плз", TriageResult))
    print(len(embed("тест")))  # очікуємо 1024
