"""Апеляції. A: прийом, правила подачі, resolve_appeal(). B: triage_and_route() в кінці файлу."""
from datetime import datetime, timezone

import numpy as np
from fastapi import HTTPException

from app import db

OPEN_STATUSES = {"new", "support_queue", "court_queue"}
DECIDABLE_STATUSES = {"support_queue", "court_queue"}
MAX_ATTEMPTS = 3

APPEAL_ERRORS = {
    400: "Інцидент не знайдено, він належить іншому юзеру або вже неактивний",
    409: "По цьому інциденту вже є апеляція на розгляді",
    429: "Ліміт апеляцій по цьому інциденту вичерпано (максимум 3)",
}


def is_active(incident: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if incident.get("revoked_at") is not None:
        return False
    expires_at = incident.get("expires_at")
    return expires_at is None or expires_at > now


def check_can_appeal(user_id: int | None, incident: dict | None, appeals: list[dict],
                     now: datetime | None = None) -> int | None:
    """None — апеляцію можна подати, інакше HTTP-код помилки (400 / 409 / 429).

    appeals — усі попередні апеляції по цьому інциденту (потрібне лише поле status).
    """
    if user_id is None or incident is None or incident["user_id"] != user_id:
        return 400
    if not is_active(incident, now):
        return 400
    if any(a["status"] in OPEN_STATUSES for a in appeals):
        return 409
    if len(appeals) >= MAX_ATTEMPTS:
        return 429
    return None


def submit_appeal(username: str, incident_id: int, text: str, contact: str | None) -> dict:
    with db.conn() as c, c.transaction():
        user = c.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
        # FOR UPDATE: дві одночасні подачі по одному інциденту не пройдуть обидві
        incident = c.execute("SELECT * FROM incidents WHERE id = %s FOR UPDATE",
                             (incident_id,)).fetchone()
        previous = c.execute("SELECT status FROM appeals WHERE incident_id = %s",
                             (incident_id,)).fetchall()
        code = check_can_appeal(user["id"] if user else None, incident, previous)
        if code:
            raise HTTPException(code, APPEAL_ERRORS[code])
        return c.execute(
            """INSERT INTO appeals (incident_id, user_id, text, contact, attempt_no)
               VALUES (%s, %s, %s, %s, %s) RETURNING id, status, attempt_no""",
            (incident_id, user["id"], text, contact, len(previous) + 1),
        ).fetchone()


def precedent_summary(rule: str, reason: str, triage: dict | None, appeal_text: str) -> str:
    claim = (triage or {}).get("summary") or appeal_text[:200]
    return f"{rule}: {reason}. Апеляція: {claim}"


def resolve_appeal(appeal_id: int, decision: str, by: str, reasoning: str) -> None:
    """Фінальне рішення людини (support | streamer): розбан при overturn + новий прецедент."""
    with db.conn() as c:
        with c.transaction():
            a = c.execute(
                """SELECT a.id, a.status, a.incident_id, a.text, a.triage, i.rule, i.reason
                   FROM appeals a JOIN incidents i ON i.id = a.incident_id
                   WHERE a.id = %s FOR UPDATE OF a""",
                (appeal_id,),
            ).fetchone()
            if not a:
                raise HTTPException(404, "Апеляцію не знайдено")
            if a["status"] not in DECIDABLE_STATUSES:
                raise HTTPException(409, f"Апеляція в статусі {a['status']}, рішення вже не приймається")
            if decision == "overturn":
                c.execute("UPDATE incidents SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
                          (a["incident_id"],))
            status = "resolved_overturned" if decision == "overturn" else "resolved_upheld"
            c.execute("UPDATE appeals SET status = %s, resolved_by = %s, resolved_at = now() WHERE id = %s",
                      (status, by, appeal_id))

        # Прецедент пишемо після коміту: якщо API ембедингів впаде, розбан усе одно діє
        summary = precedent_summary(a["rule"], a["reason"], a["triage"], a["text"])
        try:
            from app.llm import embed
            vec = np.array(embed(summary))
        except Exception as e:
            print(f"precedent for appeal {appeal_id} skipped: {e}")
            return
        c.execute(
            """INSERT INTO precedents (appeal_id, summary, decision, reasoning, decided_by, embedding)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (appeal_id, summary, decision, reasoning, by, vec),
        )


# ---------------------------------------------------------------------------
# B: triage_and_route
# ---------------------------------------------------------------------------
import logging

from psycopg.types.json import Jsonb

from app import db, policy, prompts
from app.llm import LLMError, chat_json
from app.schemas import TriageResult

_log = logging.getLogger(__name__)

_ROUTE = {"boring": "rejected_auto", "disputed": "support_queue", "interesting": "court_queue"}


def triage_and_route(appeal_id: int) -> str | None:
    """Classify a new appeal and move it to rejected_auto | support_queue | court_queue.

    Runs in FastAPI BackgroundTasks. For court_queue the case is prepared right here (we are
    already off the request thread). Returns the new status, or None if the appeal was not 'new'.
    """
    with db.conn() as c:
        ctx = c.execute(
            """SELECT a.id, a.text, a.attempt_no, a.user_id, a.status,
                      i.action, i.duration_min, i.rule, i.reason, i.user_summary,
                      ch.name AS channel, m.text AS message_text, u.username
               FROM appeals a
               JOIN incidents i ON i.id = a.incident_id
               JOIN channels ch ON ch.id = i.channel_id
               JOIN users u ON u.id = a.user_id
               LEFT JOIN messages m ON m.id = i.message_id
               WHERE a.id = %s""",
            (appeal_id,),
        ).fetchone()
        if not ctx or ctx["status"] != "new":
            return None
        previous = c.execute(
            """SELECT a.id, a.text, a.status, ch.name AS channel, i.rule
               FROM appeals a
               JOIN incidents i ON i.id = a.incident_id
               JOIN channels ch ON ch.id = i.channel_id
               WHERE a.user_id = %s AND a.id <> %s
               ORDER BY a.created_at""",
            (ctx["user_id"], appeal_id),
        ).fetchall()

    try:
        t = chat_json(
            "triage",
            prompts.TRIAGE,
            prompts.triage_user(ctx, ctx["attempt_no"], previous,
                                 f"{ctx['username']}: {ctx['message_text'] or ''}", ctx["text"]),
            TriageResult,
            0.2,
        )
    except LLMError as e:
        _log.warning("triage failed for appeal %s → support_queue: %s", appeal_id, e)
        with db.conn() as c:            # не знаєш — віддай людині
            c.execute("UPDATE appeals SET status = 'support_queue' WHERE id = %s AND status = 'new'", (appeal_id,))
        return "support_queue"

    category = policy.guard_triage(t.category, ctx["rule"])
    status = _ROUTE[category]
    resolved = status == "rejected_auto"
    with db.conn() as c:
        row = c.execute(
            """UPDATE appeals
               SET status = %s, triage = %s,
                   resolved_by = CASE WHEN %s THEN 'ai' ELSE resolved_by END,
                   resolved_at = CASE WHEN %s THEN now() ELSE resolved_at END
               WHERE id = %s AND status = 'new'
               RETURNING id""",
            (status, Jsonb(t.model_dump()), resolved, resolved, appeal_id),
        ).fetchone()
    _log.info("appeal %s: triage=%s (guarded %s, interest=%d, sincerity=%d) → %s", appeal_id, t.category,
              category, t.interest_score, t.sincerity, status)
    if row and status == "court_queue":
        from app import court            # local import: court.py imports this module for resolve_appeal

        court.prepare_case(appeal_id)     # уже у фоновому потоці, тож можна викликати синхронно
    return status
