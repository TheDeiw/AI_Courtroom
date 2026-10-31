"""Two-stage chat moderation (ARCHITECTURE.md, 9.1).

loop_forever() runs as a daemon thread (started in main.py): every 3 s tick() takes up to 30 new
messages, the fast prefilter flags suspicious ones, the moderator decides about each flagged one,
policy.guard_moderation() clamps the decision, and only then the code applies it.

Safe defaults (9.5): prefilter error → whole batch 'ok' (fail-open, chat never hangs);
moderator error → 'acquitted' (presumption of innocence).
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app import db, policy, prompts
from app.llm import LLMError, chat_json
from app.schemas import ModerationDecision, PrefilterResult

log = logging.getLogger(__name__)

BATCH_LIMIT = 30
CHAT_WINDOW = 15
USER_HISTORY = 20
TICK_SECONDS = 3


def loop_forever():                       # стартує в main.py як daemon-потік
    while True:
        try:
            tick()
        except Exception as e:            # потік не повинен падати
            log.exception("moderation tick failed: %s", e)
        time.sleep(TICK_SECONDS)


def tick() -> int:
    """Process one batch of new messages. Returns how many messages were taken."""
    with db.conn() as c:
        batch = c.execute(
            """SELECT m.id, u.username, m.text, m.channel_id, ch.rules
               FROM messages m
               JOIN users u ON u.id = m.user_id
               JOIN channels ch ON ch.id = m.channel_id
               WHERE m.status = 'new'
               ORDER BY m.id LIMIT %s""",
            (BATCH_LIMIT,),
        ).fetchall()
    if not batch:
        return 0

    # Prefilter per channel: every channel has its own rules.
    by_channel: dict[int, list[dict]] = defaultdict(list)
    for m in batch:
        by_channel[m["channel_id"]].append(m)

    flagged: dict[int, str] = {}
    for msgs in by_channel.values():
        flagged.update(prefilter(msgs, msgs[0]["rules"]))

    ids = [m["id"] for m in batch]
    flagged_ids = list(flagged)
    with db.conn() as c, c.transaction():
        c.execute("UPDATE messages SET status = 'ok' WHERE id = ANY(%s) AND NOT (id = ANY(%s)) AND status = 'new'",
                  (ids, flagged_ids))
        if flagged_ids:
            c.execute("UPDATE messages SET status = 'flagged' WHERE id = ANY(%s) AND status = 'new'",
                      (flagged_ids,))
    if flagged:
        log.info("prefilter: %d/%d flagged %s", len(flagged), len(batch), flagged)

    for msg_id, reason in flagged.items():
        try:
            analyze_and_apply(msg_id, reason)
        except Exception as e:            # one bad message must not block the rest of the batch
            log.exception("moderation of message %s failed: %s", msg_id, e)
    return len(batch)


def prefilter(msgs: list[dict], rules: str) -> dict[int, str]:
    """{message_id: reason} for suspicious messages. Unknown ids from the model are ignored."""
    try:
        res = chat_json(
            "prefilter",
            prompts.prefilter_system(rules),
            prompts.prefilter_user({"id": m["id"], "user": m["username"], "text": m["text"]} for m in msgs),
            PrefilterResult,
            0.1,
        )
    except LLMError as e:
        log.warning("prefilter failed, batch passes (fail-open): %s", e)
        return {}                         # fail-open: чат не зависає, якщо префільтр зламався
    known = {m["id"] for m in msgs}
    return {f.id: f.reason for f in res.flagged if f.id in known}


def build_moderator_context(c, msg_id: int) -> dict | None:
    """Everything the moderator needs about one message: channel, chat window, history, incidents."""
    msg = c.execute(
        """SELECT m.id, m.text, m.user_id, m.channel_id, u.username,
                  ch.name AS channel, ch.rules, ch.stream_context
           FROM messages m
           JOIN users u ON u.id = m.user_id
           JOIN channels ch ON ch.id = m.channel_id
           WHERE m.id = %s""",
        (msg_id,),
    ).fetchone()
    if not msg:
        return None
    window = c.execute(
        """SELECT u.username, m.text FROM messages m JOIN users u ON u.id = m.user_id
           WHERE m.channel_id = %s AND m.id < %s
           ORDER BY m.id DESC LIMIT %s""",
        (msg["channel_id"], msg_id, CHAT_WINDOW),
    ).fetchall()
    history = c.execute(
        """SELECT ch.name AS channel, m.text FROM messages m JOIN channels ch ON ch.id = m.channel_id
           WHERE m.user_id = %s AND m.id <> %s
           ORDER BY m.id DESC LIMIT %s""",
        (msg["user_id"], msg_id, USER_HISTORY),
    ).fetchall()
    incidents = c.execute(
        """SELECT ch.name AS channel, i.action, i.duration_min, i.rule, i.reason, i.created_at, i.revoked_at
           FROM incidents i JOIN channels ch ON ch.id = i.channel_id
           WHERE i.user_id = %s
           ORDER BY i.created_at DESC LIMIT 20""",
        (msg["user_id"],),
    ).fetchall()
    return {
        "msg": msg,
        "chat_window": [(r["username"], r["text"]) for r in reversed(window)],
        "user_history": [(r["channel"], r["text"]) for r in reversed(history)],
        "past_incidents": incidents,
    }


def analyze_and_apply(msg_id: int, prefilter_reason: str) -> str:
    """Moderator decision for one flagged message, clamped by policy and applied. Returns the action."""
    with db.conn() as c:
        ctx = build_moderator_context(c, msg_id)
    if ctx is None:
        return "missing"
    msg = ctx["msg"]
    try:
        d = chat_json(
            "moderator",
            prompts.moderator_system(msg["channel"], msg["stream_context"], msg["rules"]),
            prompts.moderator_user(ctx["chat_window"], msg["username"], msg["text"], prefilter_reason,
                                   ctx["user_history"], ctx["past_incidents"]),
            ModerationDecision,
            0.2,
        )
    except LLMError as e:
        log.warning("moderator failed for message %s, acquitted: %s", msg_id, e)
        _set_status(msg_id, "acquitted")  # презумпція невинуватості
        return "acquit"

    raw_decision = d.decision
    d = policy.guard_moderation(d)
    log.info("message %s by %s: model=%s → %s (rule=%s, conf=%.2f, %s min)", msg_id, msg["username"],
             raw_decision, d.decision, d.rule, d.confidence, d.duration_min)
    if d.decision == "acquit":
        _set_status(msg_id, "acquitted")
        return "acquit"

    is_timeout = d.decision == "timeout"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=d.duration_min) if is_timeout else None
    with db.conn() as c, c.transaction():
        c.execute("UPDATE messages SET status = 'removed' WHERE id = %s", (msg_id,))
        c.execute(
            """INSERT INTO incidents (user_id, channel_id, message_id, action, duration_min, rule, reason,
                                      user_summary, confidence, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (msg["user_id"], msg["channel_id"], msg_id, d.decision, d.duration_min if is_timeout else None,
             d.rule, d.reason, d.user_summary, d.confidence, expires_at),
        )
    return d.decision


def _set_status(msg_id: int, status: str) -> None:
    with db.conn() as c:
        c.execute("UPDATE messages SET status = %s WHERE id = %s", (status, msg_id))
