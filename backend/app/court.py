"""AI court (ARCHITECTURE.md, 9.4).

prepare_case(appeal_id) runs in the background right after triage: defense and prosecution in
parallel → top-3 precedents from pgvector → judge → scripted dialogue (verdict is fixed) →
Kokoro voice-over → court_cases.status = 'ready'. Any error → 'failed' (admin shows «Перегенерувати»).

The raw offensive message goes to the attorneys and the judge (inside <untrusted_*>), but never to
the dialogue writer and never into the CourtCase JSON.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from psycopg.types.json import Jsonb

from app import appeals, db, prompts, tts
from app.llm import LLMError, chat_json, embed
from app.schemas import Dialogue, Pleading, Verdict

log = logging.getLogger(__name__)

CASE_CHAT_WINDOW = 5


class CaseNotReady(Exception):
    """confirm() on a case that is not in status 'ready' (API answers 409)."""


# ---------------------------------------------------------------------------
# Case file
# ---------------------------------------------------------------------------

def load_case(appeal_id: int) -> dict:
    """All facts about the appeal from the DB (user history across all channels)."""
    with db.conn() as c:
        ctx = c.execute(
            """SELECT a.id AS appeal_id, a.text AS appeal_text, a.attempt_no, a.triage, a.user_id,
                      a.incident_id, u.username, u.created_at AS user_created_at,
                      i.action, i.duration_min, i.rule, i.reason, i.user_summary, i.confidence,
                      i.message_id, i.channel_id, i.created_at AS incident_created_at,
                      ch.name AS channel, ch.stream_context, m.text AS message_text
               FROM appeals a
               JOIN users u ON u.id = a.user_id
               JOIN incidents i ON i.id = a.incident_id
               JOIN channels ch ON ch.id = i.channel_id
               LEFT JOIN messages m ON m.id = i.message_id
               WHERE a.id = %s""",
            (appeal_id,),
        ).fetchone()
        if not ctx:
            raise ValueError(f"appeal {appeal_id} not found")
        uid = ctx["user_id"]
        window = c.execute(
            """SELECT u.username, m.text FROM messages m JOIN users u ON u.id = m.user_id
               WHERE m.channel_id = %s AND m.id < %s ORDER BY m.id DESC LIMIT %s""",
            (ctx["channel_id"], ctx["message_id"], CASE_CHAT_WINDOW),
        ).fetchall() if ctx["message_id"] else []
        ctx["chat_window"] = window[::-1]
        ctx["channels"] = [r["name"] for r in c.execute(
            """SELECT ch.name FROM channels ch
               WHERE ch.id IN (SELECT channel_id FROM messages WHERE user_id = %s
                               UNION SELECT channel_id FROM incidents WHERE user_id = %s)
               ORDER BY ch.name""",
            (uid, uid),
        ).fetchall()]
        ctx["message_stats"] = c.execute(
            """SELECT count(*) AS total, count(*) FILTER (WHERE status = 'removed') AS removed
               FROM messages WHERE user_id = %s""",
            (uid,),
        ).fetchone()
        ctx["past_incidents"] = c.execute(
            """SELECT ch.name AS channel, i.action, i.duration_min, i.rule, i.reason, i.created_at, i.revoked_at
               FROM incidents i JOIN channels ch ON ch.id = i.channel_id
               WHERE i.user_id = %s AND i.id <> %s ORDER BY i.created_at""",
            (uid, ctx["incident_id"]),
        ).fetchall()
        ctx["past_appeals"] = c.execute(
            """SELECT a.id, a.text, a.status, ch.name AS channel, i.rule
               FROM appeals a JOIN incidents i ON i.id = a.incident_id JOIN channels ch ON ch.id = i.channel_id
               WHERE a.user_id = %s AND a.id <> %s ORDER BY a.created_at""",
            (uid, appeal_id),
        ).fetchall()
    ctx["triage"] = ctx["triage"] or {}
    return ctx


def _punishment(ctx: dict) -> str:
    if ctx["action"] == "timeout" and ctx.get("duration_min"):
        return f"timeout {ctx['duration_min']} хв"
    return ctx["action"]


def format_case_file(ctx: dict) -> str:
    """Shared case text for defense, prosecution and judge (pure function of load_case() output)."""
    created = ctx.get("user_created_at")
    stats = ctx.get("message_stats") or {}
    lines = [
        f"Обвинувачений: {prompts.esc(ctx['username'])}, акаунт від {prompts.fmt_date(created)}, "
        f"канали: {', '.join(ctx.get('channels') or [ctx['channel']])}",
        f"Повідомлень в історії: {stats.get('total', 0)} (видалено модератором: {stats.get('removed', 0)})",
        f"Порушення на каналі [{ctx['channel']}]: {_punishment(ctx)}, rule={ctx['rule']}, "
        f"reason={prompts.esc(ctx['reason'])}, confidence={ctx['confidence']:.2f}",
        f"Оцінка модератора: {prompts.esc(ctx['user_summary'])}",
        prompts.untrusted("flagged_message", f"{ctx['username']}: {ctx.get('message_text') or prompts.EMPTY}"),
        prompts.untrusted("chat_window", "\n".join(
            f"{r['username']}: {r['text']}" for r in ctx.get("chat_window") or []) or prompts.EMPTY),
        "Минулі інциденти:",
        prompts.format_incidents(ctx.get("past_incidents") or []),
        "Минулі апеляції:",
    ]
    past = ctx.get("past_appeals") or []
    if not past:
        lines.append(prompts.EMPTY)
    for a in past:
        lines.append(f"#{a['id']} ({prompts.esc(a['channel'])}, rule={a['rule']}) → {a['status']}")
        lines.append(prompts.untrusted("previous_appeal", a["text"] or ""))
    lines += [
        f"Поточна апеляція (спроба {ctx['attempt_no']}):",
        prompts.untrusted("appeal", ctx["appeal_text"]),
        f"Суть апеляції за тріажем: {prompts.esc(ctx['triage'].get('summary', ''))}",
        f"Контекст стріму: {prompts.esc(ctx.get('stream_context') or prompts.EMPTY)}",
    ]
    return "\n".join(lines)


def build_case_file(appeal_id: int) -> str:
    """Спільний текст справи для адвоката, прокурора й судді."""
    return format_case_file(load_case(appeal_id))


def case_summary(ctx: dict) -> str:
    """Short case description for the dialogue writer: no raw message, no personal data but the nick."""
    past = ctx.get("past_appeals") or []
    lines = [
        f"Справа №{ctx['incident_id']}: «Чат проти {prompts.esc(ctx['username'])}».",
        f"Покарання: {_punishment(ctx)} на каналі {ctx['channel']}, правило {ctx['rule']}: "
        f"{prompts.esc(ctx['reason'])}.",
        f"Суть апеляції (спроба {ctx['attempt_no']}): {prompts.esc(ctx['triage'].get('summary', ''))}",
        prompts.untrusted("appeal", ctx["appeal_text"]),
        f"Минулих інцидентів: {len(ctx.get('past_incidents') or [])}; "
        f"каналів в історії: {len(ctx.get('channels') or [])}; попередніх апеляцій: {len(past)}.",
    ]
    for a in past:
        lines.append(f"Попередня апеляція #{a['id']} ({a['channel']}) → {a['status']}:")
        lines.append(prompts.untrusted("previous_appeal", a["text"] or ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Precedents (pgvector)
# ---------------------------------------------------------------------------

def precedent_query(ctx: dict) -> str:
    # Same shape as the summary resolve_appeal() stores, so vectors are comparable.
    return f"{ctx['rule']}: {ctx['reason']}. Апеляція: {ctx['triage'].get('summary', '')}"


def find_precedents(text: str, k: int = 3) -> list[dict]:
    """Top-k similar precedents by cosine distance. Precedents are P1: on embed error → []."""
    try:
        vec = np.array(embed(text))
    except LLMError as e:
        log.warning("precedent search skipped: %s", e)
        return []
    with db.conn() as c:
        return c.execute(
            "SELECT id, summary, decision, reasoning FROM precedents ORDER BY embedding <=> %s LIMIT %s",
            (vec, k),
        ).fetchall()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def prepare_case(appeal_id: int) -> int:
    with db.conn() as c:
        row = c.execute(
            """INSERT INTO court_cases (appeal_id) VALUES (%s)
               ON CONFLICT (appeal_id) DO UPDATE SET status = 'preparing', error = NULL
               WHERE court_cases.status <> 'confirmed'
               RETURNING id""",
            (appeal_id,),
        ).fetchone()
        if row is None:                   # confirmed cases are closed: never regenerate them
            return c.execute("SELECT id FROM court_cases WHERE appeal_id = %s", (appeal_id,)).fetchone()["id"]
    case_id = row["id"]
    t0 = time.monotonic()
    try:
        ctx = load_case(appeal_id)
        case_file = format_case_file(ctx)
        with ThreadPoolExecutor(2) as ex:      # адвокат і прокурор паралельно
            fd = ex.submit(chat_json, "defense", prompts.DEFENSE, case_file, Pleading, 0.6)
            fp = ex.submit(chat_json, "prosecution", prompts.PROSECUTION, case_file, Pleading, 0.6)
            defense, prosecution = fd.result(), fp.result()

        precedents = find_precedents(precedent_query(ctx))
        verdict = chat_json(
            "judge", prompts.JUDGE,
            prompts.judge_user(case_file, defense.model_dump(), prosecution.model_dump(), precedents),
            Verdict, 0.2,
        )
        known = {p["id"] for p in precedents}
        verdict.precedent_ids = [i for i in dict.fromkeys(verdict.precedent_ids) if i in known]

        dialogue = chat_json(
            "dialogue", prompts.dialogue_system(verdict.decision),
            prompts.dialogue_user(case_summary(ctx), defense.model_dump(), prosecution.model_dump(),
                                  verdict.model_dump()),
            Dialogue, 0.8,
        )
        if not dialogue.lines:
            raise LLMError("dialogue: no lines")
        lines = tts.generate_dialogue_audio(dialogue, case_id)  # adds audio_url, never raises

        with db.conn() as c:
            c.execute(
                """UPDATE court_cases
                   SET defense = %s, prosecution = %s, judge = %s, dialogue = %s, precedent_ids = %s,
                       status = 'ready', error = NULL
                   WHERE id = %s""",
                (Jsonb(defense.model_dump()), Jsonb(prosecution.model_dump()), Jsonb(verdict.model_dump()),
                 Jsonb(lines), verdict.precedent_ids, case_id),
            )
        log.info("case %s (appeal %s) ready in %.0fs: %s, %d lines, precedents %s", case_id, appeal_id,
                 time.monotonic() - t0, verdict.decision, len(lines), verdict.precedent_ids)
    except Exception as e:
        log.exception("case %s (appeal %s) failed: %s", case_id, appeal_id, e)
        with db.conn() as c:
            c.execute("UPDATE court_cases SET status = 'failed', error = %s WHERE id = %s",
                      (f"{type(e).__name__}: {e}"[:2000], case_id))
    return case_id


def confirm(case_id: int, final_decision: str) -> None:
    """Streamer confirms (or changes) the verdict: resolve the appeal and close the case."""
    if final_decision not in ("uphold", "overturn"):
        raise ValueError(f"bad final_decision: {final_decision!r}")
    with db.conn() as c:                  # claim the case atomically: a double click resolves once
        row = c.execute(
            """UPDATE court_cases SET status = 'confirmed', final_decision = %s
               WHERE id = %s AND status = 'ready' RETURNING appeal_id, judge""",
            (final_decision, case_id),
        ).fetchone()
    if row is None:
        raise CaseNotReady(case_id)
    judge = row["judge"] or {}
    reasoning = judge.get("reasoning", "") + ("" if final_decision == judge.get("decision")
                                              else " (стример змінив рішення)")
    try:
        appeals.resolve_appeal(row["appeal_id"], final_decision, by="streamer", reasoning=reasoning)
    except Exception:
        with db.conn() as c:              # roll back the claim so the streamer can retry
            c.execute("UPDATE court_cases SET status = 'ready', final_decision = NULL WHERE id = %s", (case_id,))
        raise
