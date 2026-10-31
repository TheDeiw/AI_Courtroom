import importlib
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app import appeals, db

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

AppealStatus = Literal["new", "rejected_auto", "support_queue", "court_queue",
                       "resolved_upheld", "resolved_overturned"]
APPEAL_STAT_KEYS = ["rejected_auto", "support_queue", "court_queue",
                    "resolved_upheld", "resolved_overturned"]


def b_module(name: str):
    """Модулі B (moderation, court) можуть бути ще не запушені — бекенд тоді все одно стартує."""
    try:
        return importlib.import_module(f"app.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"app.{name}":
            raise  # модуль є, але в ньому зламаний імпорт — це треба бачити
        return None


def require_court():
    court = b_module("court")
    if court is None:
        raise HTTPException(503, "Модуль суду (app.court) ще не готовий")
    return court


@asynccontextmanager
async def lifespan(app):
    db.init_schema()
    moderation = b_module("moderation")
    if moderation:
        threading.Thread(target=moderation.loop_forever, daemon=True).start()
    else:
        print("WARNING: app.moderation not found, moderation loop is not running")
    yield


app = FastAPI(title="AI Courtroom", lifespan=lifespan)
api = APIRouter(prefix="/api")


# ---------- тіла запитів ----------

class MessageIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    channel: str = Field(min_length=1, max_length=50)
    username: str = Field(min_length=1, max_length=50)
    text: str = Field(min_length=1, max_length=500)


class ChannelIn(BaseModel):
    rules: str = ""
    stream_context: str = ""


class AppealIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    username: str = Field(min_length=1, max_length=50)
    incident_id: int
    text: str = Field(min_length=20, max_length=2000)
    contact: str | None = Field(default=None, max_length=200)


class DecisionIn(BaseModel):
    decision: Literal["uphold", "overturn"]
    note: str = ""


class ConfirmIn(BaseModel):
    final_decision: Literal["uphold", "overturn"]


# ---------- допоміжне ----------

def get_channel_id(c, name: str) -> int:
    row = c.execute("SELECT id FROM channels WHERE name = %s", (name,)).fetchone()
    if not row:
        raise HTTPException(404, f"Канал '{name}' не знайдено")
    return row["id"]


INCIDENT_SQL = """
SELECT i.id, u.username, ch.name AS channel, i.action, i.duration_min, i.rule, i.reason,
       round(i.confidence::numeric, 2)::float AS confidence, m.text AS message_text,
       i.created_at, i.expires_at,
       (i.revoked_at IS NULL AND (i.expires_at IS NULL OR i.expires_at > now())) AS active
FROM incidents i
JOIN users u ON u.id = i.user_id
JOIN channels ch ON ch.id = i.channel_id
LEFT JOIN messages m ON m.id = i.message_id
"""


# ---------- health ----------

@api.get("/health")
def health():
    return {"ok": True}


# ---------- чат ----------

@api.post("/messages", status_code=201)
def post_message(body: MessageIn):
    with db.conn() as c:
        channel_id = get_channel_id(c, body.channel)
        c.execute("INSERT INTO users (username) VALUES (%s) ON CONFLICT (username) DO NOTHING",
                  (body.username,))
        user_id = c.execute("SELECT id FROM users WHERE username = %s", (body.username,)).fetchone()["id"]
        punishment = c.execute(
            """SELECT * FROM incidents
               WHERE user_id = %s AND channel_id = %s
                 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, channel_id),
        ).fetchone()
        if punishment:
            raise HTTPException(403, jsonable_encoder({
                "banned": True, "incident_id": punishment["id"], "action": punishment["action"],
                "reason": punishment["reason"], "expires_at": punishment["expires_at"],
            }))
        return c.execute(
            "INSERT INTO messages (channel_id, user_id, text) VALUES (%s, %s, %s) RETURNING id, status",
            (channel_id, user_id, body.text),
        ).fetchone()


@api.get("/messages")
def get_messages(channel: str = "main", limit: int = Query(50, ge=1, le=200)):
    with db.conn() as c:
        channel_id = get_channel_id(c, channel)
        rows = c.execute(
            """SELECT m.id, u.username, m.text, m.status, m.created_at
               FROM messages m JOIN users u ON u.id = m.user_id
               WHERE m.channel_id = %s
               ORDER BY m.created_at DESC, m.id DESC LIMIT %s""",
            (channel_id, limit),
        ).fetchall()
    return rows[::-1]  # від старих до нових


# ---------- канали ----------

@api.get("/channels/{name}")
def get_channel(name: str):
    with db.conn() as c:
        row = c.execute("SELECT name, rules, stream_context FROM channels WHERE name = %s",
                        (name,)).fetchone()
    if not row:
        raise HTTPException(404, f"Канал '{name}' не знайдено")
    return row


@api.put("/channels/{name}")
def put_channel(name: str, body: ChannelIn):
    with db.conn() as c:
        c.execute(
            """INSERT INTO channels (name, rules, stream_context) VALUES (%s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET rules = EXCLUDED.rules,
                                                stream_context = EXCLUDED.stream_context""",
            (name, body.rules, body.stream_context),
        )
    return {"ok": True}


# ---------- інциденти ----------

@api.get("/incidents")
def list_incidents(channel: str | None = None):
    with db.conn() as c:
        return c.execute(
            INCIDENT_SQL + " WHERE (%(ch)s::text IS NULL OR ch.name = %(ch)s) ORDER BY i.created_at DESC, i.id DESC",
            {"ch": channel},
        ).fetchall()


@api.get("/incidents/{incident_id}")
def get_incident(incident_id: int):
    with db.conn() as c:
        row = c.execute(INCIDENT_SQL + " WHERE i.id = %s", (incident_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Інцидент не знайдено")
    return row


# ---------- апеляції ----------

@api.post("/appeals", status_code=201)
def post_appeal(body: AppealIn, background: BackgroundTasks):
    row = appeals.submit_appeal(body.username, body.incident_id, body.text, body.contact)
    triage = getattr(appeals, "triage_and_route", None)  # функція B
    if triage:
        background.add_task(triage, row["id"])
    else:
        print(f"WARNING: appeals.triage_and_route not found, appeal {row['id']} stays 'new'")
    return row


@api.get("/appeals/{appeal_id}")
def get_appeal(appeal_id: int):
    with db.conn() as c:
        a = c.execute("SELECT id, status, attempt_no, triage FROM appeals WHERE id = %s",
                      (appeal_id,)).fetchone()
    if not a:
        raise HTTPException(404, "Апеляцію не знайдено")
    public_reason = (a["triage"] or {}).get("reason") if a["status"] == "rejected_auto" else None
    return {"id": a["id"], "status": a["status"], "attempt_no": a["attempt_no"],
            "public_reason": public_reason}


@api.get("/appeals")
def list_appeals(status: AppealStatus | None = None):
    with db.conn() as c:
        return c.execute(
            """SELECT a.id, u.username, a.status, a.attempt_no, a.text, a.created_at,
                      json_build_object('id', i.id, 'action', i.action,
                                        'rule', i.rule, 'reason', i.reason) AS incident,
                      a.triage,
                      CASE WHEN cc.id IS NULL THEN NULL
                           ELSE json_build_object('id', cc.id, 'status', cc.status) END AS "case"
               FROM appeals a
               JOIN users u ON u.id = a.user_id
               JOIN incidents i ON i.id = a.incident_id
               LEFT JOIN court_cases cc ON cc.appeal_id = a.id
               WHERE (%(st)s::text IS NULL OR a.status = %(st)s)
               ORDER BY a.created_at, a.id""",
            {"st": status},
        ).fetchall()


@api.post("/appeals/{appeal_id}/decision")
def decide_appeal(appeal_id: int, body: DecisionIn):
    # 404 / 409 (не support_queue чи court_queue) кидає сам resolve_appeal під блокуванням рядка
    reasoning = body.note.strip() or "Рішення модератора підтримки"
    appeals.resolve_appeal(appeal_id, body.decision, by="support", reasoning=reasoning)
    return {"ok": True}


# ---------- суд ----------

@api.post("/court/{appeal_id}/prepare", status_code=202)
def prepare_court_case(appeal_id: int, background: BackgroundTasks):
    court = require_court()
    with db.conn() as c:
        a = c.execute("SELECT status FROM appeals WHERE id = %s", (appeal_id,)).fetchone()
        if not a:
            raise HTTPException(404, "Апеляцію не знайдено")
        if a["status"] != "court_queue":
            raise HTTPException(409, f"Апеляція в статусі {a['status']}, а не court_queue")
        case = c.execute(
            """INSERT INTO court_cases (appeal_id) VALUES (%s)
               ON CONFLICT (appeal_id) DO UPDATE SET status = 'preparing', error = NULL
               RETURNING id""",
            (appeal_id,),
        ).fetchone()
    background.add_task(court.prepare_case, appeal_id)
    return {"case_id": case["id"]}


@api.get("/court/cases/{case_id}")
def get_court_case(case_id: int):
    with db.conn() as c:
        cc = c.execute(
            """SELECT cc.*, a.attempt_no, a.triage, a.user_id, u.username,
                      i.id AS incident_id, i.action, i.rule, i.reason
               FROM court_cases cc
               JOIN appeals a ON a.id = cc.appeal_id
               JOIN users u ON u.id = a.user_id
               JOIN incidents i ON i.id = a.incident_id
               WHERE cc.id = %s""",
            (case_id,),
        ).fetchone()
        if not cc:
            raise HTTPException(404, "Справу не знайдено")
        defendant = c.execute(
            """SELECT
                 (SELECT count(*) FROM incidents WHERE user_id = %(u)s) AS incidents_total,
                 (SELECT coalesce(array_agg(name ORDER BY name), '{}') FROM channels WHERE id IN (
                     SELECT channel_id FROM messages WHERE user_id = %(u)s
                     UNION SELECT channel_id FROM incidents WHERE user_id = %(u)s)) AS channels,
                 (SELECT count(*) FROM appeals WHERE user_id = %(u)s AND id < %(a)s) AS previous_appeals""",
            {"u": cc["user_id"], "a": cc["appeal_id"]},
        ).fetchone()
        judge = cc["judge"]
        ids = (judge or {}).get("precedent_ids") or cc["precedent_ids"]
        precedents = c.execute(
            "SELECT id, summary, decision FROM precedents WHERE id = ANY(%s) ORDER BY id", (ids,)
        ).fetchall() if ids else []

    dialogue = cc["dialogue"]
    if isinstance(dialogue, dict):  # Dialogue.model_dump() → {"lines": [...]}
        dialogue = dialogue.get("lines", [])
    # Сирий текст повідомлення сюди свідомо не потрапляє (розділ 7.3)
    return {
        "id": cc["id"],
        "status": cc["status"],
        "appeal": {"id": cc["appeal_id"], "attempt_no": cc["attempt_no"],
                   "summary": (cc["triage"] or {}).get("summary")},
        "defendant": {"username": cc["username"], **defendant},
        "incident": {"id": cc["incident_id"], "action": cc["action"],
                     "rule": cc["rule"], "reason": cc["reason"]},
        "defense": cc["defense"],
        "prosecution": cc["prosecution"],
        "judge": judge,
        "precedents": precedents,
        "dialogue": dialogue or [],
        "final_decision": cc["final_decision"],
    }


@api.post("/court/cases/{case_id}/confirm")
def confirm_court_case(case_id: int, body: ConfirmIn):
    court = require_court()
    with db.conn() as c:
        cc = c.execute("SELECT status FROM court_cases WHERE id = %s", (case_id,)).fetchone()
    if not cc:
        raise HTTPException(404, "Справу не знайдено")
    if cc["status"] != "ready":
        raise HTTPException(409, f"Справа в статусі {cc['status']}, підтвердити можна лише ready")
    court.confirm(case_id, body.final_decision)
    return {"ok": True}


# ---------- статистика ----------

@api.get("/stats")
def stats(channel: str = "main"):
    with db.conn() as c:
        channel_id = get_channel_id(c, channel)
        msg = c.execute(
            """SELECT count(*) AS messages_total,
                      count(*) FILTER (WHERE status IN ('flagged', 'acquitted', 'removed')) AS prefilter_flagged,
                      count(*) FILTER (WHERE status = 'acquitted') AS deep_acquitted
               FROM messages WHERE channel_id = %s""",
            (channel_id,),
        ).fetchone()
        inc = c.execute(
            """SELECT count(*) FILTER (WHERE action = 'timeout') AS timeouts,
                      count(*) FILTER (WHERE action = 'ban') AS bans
               FROM incidents WHERE channel_id = %s""",
            (channel_id,),
        ).fetchone()
        rows = c.execute(
            """SELECT a.status, count(*) AS n FROM appeals a
               JOIN incidents i ON i.id = a.incident_id
               WHERE i.channel_id = %s GROUP BY a.status""",
            (channel_id,),
        ).fetchall()
        avg_min = c.execute(
            """SELECT avg(extract(epoch FROM a.resolved_at - a.created_at)) / 60 AS m
               FROM appeals a JOIN incidents i ON i.id = a.incident_id
               WHERE i.channel_id = %s AND a.resolved_at IS NOT NULL""",
            (channel_id,),
        ).fetchone()["m"]
        precedents_total = c.execute("SELECT count(*) AS n FROM precedents").fetchone()["n"]

    by_status = {r["status"]: r["n"] for r in rows}
    appeal_counts = {k: by_status.get(k, 0) for k in APPEAL_STAT_KEYS}
    closed = appeal_counts["rejected_auto"] + appeal_counts["resolved_upheld"] + appeal_counts["resolved_overturned"]
    return {
        **msg,
        **inc,
        "appeals": appeal_counts,
        # частка рішень AI, скасованих людиною, серед усіх закритих апеляцій
        "overturn_rate": round(appeal_counts["resolved_overturned"] / closed, 2) if closed else 0.0,
        "avg_resolution_min": round(float(avg_min), 1) if avg_min is not None else 0.0,
        "precedents_total": precedents_total,
    }


app.include_router(api)

# монтуємо ОСТАННІМ; папки frontend може ще не бути, поки C не запушив
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
