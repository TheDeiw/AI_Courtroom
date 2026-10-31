"""Дані для демо. Ідемпотентний: python -m app.seed (запускати перед кожним прогоном)."""
from datetime import datetime, timedelta, timezone

import numpy as np
from psycopg.types.json import Jsonb

from app import db

# =============================================================================
# ДАНІ. Чорновик A за розділом 6.5: тексти замінює D, прецеденти — B.
# Час задається як «скільки днів тому», щоб дані завжди виглядали свіжими.
# =============================================================================

CHANNELS = [
    {
        "name": "main",
        "rules": (
            "1. Без образ стримера, модераторів і глядачів.\n"
            "2. Без мови ворожнечі та дискримінації.\n"
            "3. Без спаму, посилань і реклами.\n"
            "4. Без спойлерів сюжету «Відьмака 3»: стример проходить гру вперше.\n"
            "5. Без погроз і публікації чужих особистих даних."
        ),
        "stream_context": (
            "Перше проходження «Відьмак 3: Дикий Гін». Стример не знає сюжету й просить не спойлерити. "
            "Зараз Велен і контракти на монстрів, тож у чаті багато розмов про бої, грифонів і тактики."
        ),
    },
    {
        "name": "minecraft_ua",
        "rules": "1. Без образ.\n2. Без спаму й реклами.\n3. Без гріфінгу чужих будівель.",
        "stream_context": "Виживання на спільному сервері, будуємо замок.",
    },
]

# username → скільки днів тому створено акаунт
USERS = {
    "kotyk_2007": 49,
    "toxic_vasya": 120,
    "geralt_fan": 200,
    "stream_mod": 365,
    "ciri_lover": 90,
    "plotva_rider": 60,
    "zoltan_dwarf": 30,
    "yen_and_triss": 15,
}

# Мирна історія чату: (username, channel, днів тому, текст)
MESSAGES = [
    ("kotyk_2007", "minecraft_ua", 40, "всім привіт, можна до вас на сервер?"),
    ("kotyk_2007", "minecraft_ua", 38, "збудував ферму гарбузів біля спавну"),
    ("kotyk_2007", "minecraft_ua", 35, "дякую за допомогу з порталом!"),
    ("kotyk_2007", "main", 25, "о, Відьмак! найкраща гра"),
    ("kotyk_2007", "main", 20, "Плотва знову застрягла на даху 😂"),
    ("kotyk_2007", "main", 14, "гарний стрім, дякую"),
    ("kotyk_2007", "main", 10, "може спробувати знак Ігні на тих утопців?"),
    ("kotyk_2007", "main", 7, "привіт чат"),
    ("kotyk_2007", "main", 3, "обожнюю цю музику у Велені"),
    ("kotyk_2007", "main", 1, "до завтра всім!"),
    ("geralt_fan", "main", 12, "ця гра 2015 року, а досі виглядає топ"),
    ("geralt_fan", "main", 2, "раджу випити зілля Ластівки перед боєм"),
    ("stream_mod", "main", 5, "нагадую: спойлери заборонені, стример грає вперше"),
    ("ciri_lover", "main", 4, "Цірі найкраща"),
    ("plotva_rider", "main", 4, "Плотва знову на даху"),
    ("zoltan_dwarf", "main", 2, "Золтан передає всім привіт 🍺"),
    ("yen_and_triss", "main", 1, "команда Йеннефер!"),
]

# Минулі інциденти. Текст повідомлення додається в чат зі статусом removed.
INCIDENTS = [
    {
        "key": "kotyk_spam", "username": "kotyk_2007", "channel": "minecraft_ua", "days_ago": 30,
        "message": "КУПУЙ ДІАМАНТИ ДЕШЕВО!!! заходь на мій сайт",
        "action": "timeout", "duration_min": 10, "rule": "spam",
        "reason": "Реклама й спам у чаті",
        "user_summary": "Раніше писав мирно, це перше порушення.", "confidence": 0.92,
    },
    {
        "key": "vasya_1", "username": "toxic_vasya", "channel": "main", "days_ago": 20,
        "message": "[образа глядача]",
        "action": "timeout", "duration_min": 10, "rule": "insult",
        "reason": "Образа глядача",
        "user_summary": "Часто сперечається з глядачами в агресивному тоні.", "confidence": 0.88,
    },
    {
        "key": "vasya_2", "username": "toxic_vasya", "channel": "main", "days_ago": 7,
        "message": "[образа глядача]",
        "action": "timeout", "duration_min": 30, "rule": "insult",
        "reason": "Повторна образа глядача",
        "user_summary": "Друге порушення за місяць, патерн агресії до інших глядачів.", "confidence": 0.9,
    },
]

# Минулі апеляції (по ключу інциденту)
APPEALS = [
    {
        "incident": "kotyk_spam", "days_after_incident": 0.01,
        "text": "це писав мій брат, поки я відійшов, я б такого не написав",
        "status": "resolved_upheld", "resolved_by": "support", "resolved_after_min": 25,
        "triage": {
            "category": "disputed", "interest_score": 4, "sincerity": 3,
            "reason": "Перекладає провину на брата, перевірити це неможливо",
            "summary": "Стверджує, що спам написав його брат",
        },
    },
]

# Прецеденти (decided_by='seed'). Формат summary такий самий, як у resolve_appeal()
PRECEDENTS = [
    {
        "summary": "insult: Бан за образу стримера. Апеляція: перше порушення, раніше не був токсичним, щиро вибачається",
        "decision": "overturn",
        "reasoning": "Перше порушення без попередньої токсичності та щире вибачення. "
                     "Безстроковий бан непропорційний, тому розбан з останнім попередженням.",
    },
    {
        "summary": "spam: Бан за спам посиланнями. Апеляція: третя спроба з тією самою історією «не знав правил»",
        "decision": "uphold",
        "reasoning": "Третя однакова апеляція без нових аргументів. Правила закріплені в чаті, "
                     "незнання не звільняє від відповідальності.",
    },
    {
        "summary": "spoiler: Таймаут за спойлер фіналу гри. Апеляція: «всі вже бачили фінал»",
        "decision": "uphold",
        "reasoning": "Стример проходить гру вперше, і правило про спойлери оголошене. "
                     "Те, що гра стара, правило не скасовує.",
    },
    {
        "summary": "threat: Бан за погрозу. Апеляція: це була цитата з гри, а не реальна погроза",
        "decision": "overturn",
        "reasoning": "Контекст чату підтверджує, що це репліка персонажа з гри. Реальної погрози нікому не було.",
    },
    {
        "summary": "insult: Бан за образу модератора. Апеляція: перекладає провину на іншу людину, вже повторна така історія",
        "decision": "uphold",
        "reasoning": "Юзер удруге пояснює порушення діями іншої людини. Відповідальність за акаунт несе його власник.",
    },
]

# =============================================================================


def ago(days: float = 0, minutes: float = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days, minutes=minutes)


def seed():
    db.init_schema()
    with db.conn() as c:
        with c.transaction():
            c.execute("TRUNCATE precedents, court_cases, appeals, incidents, messages, users, channels "
                      "RESTART IDENTITY CASCADE")

            channel_ids = {}
            for ch in CHANNELS:
                channel_ids[ch["name"]] = c.execute(
                    "INSERT INTO channels (name, rules, stream_context) VALUES (%s, %s, %s) RETURNING id",
                    (ch["name"], ch["rules"], ch["stream_context"]),
                ).fetchone()["id"]

            user_ids = {}
            for username, days in USERS.items():
                user_ids[username] = c.execute(
                    "INSERT INTO users (username, created_at) VALUES (%s, %s) RETURNING id",
                    (username, ago(days)),
                ).fetchone()["id"]

            for username, channel, days, text in MESSAGES:
                c.execute(
                    "INSERT INTO messages (channel_id, user_id, text, status, created_at) "
                    "VALUES (%s, %s, %s, 'ok', %s)",
                    (channel_ids[channel], user_ids[username], text, ago(days)),
                )

            incident_ids = {}
            for inc in INCIDENTS:
                created = ago(inc["days_ago"])
                msg_id = c.execute(
                    "INSERT INTO messages (channel_id, user_id, text, status, created_at) "
                    "VALUES (%s, %s, %s, 'removed', %s) RETURNING id",
                    (channel_ids[inc["channel"]], user_ids[inc["username"]], inc["message"], created),
                ).fetchone()["id"]
                expires = created + timedelta(minutes=inc["duration_min"]) if inc["action"] == "timeout" else None
                incident_ids[inc["key"]] = c.execute(
                    """INSERT INTO incidents (user_id, channel_id, message_id, action, duration_min, rule,
                                              reason, user_summary, confidence, expires_at, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (user_ids[inc["username"]], channel_ids[inc["channel"]], msg_id, inc["action"],
                     inc["duration_min"] if inc["action"] == "timeout" else None, inc["rule"],
                     inc["reason"], inc["user_summary"], inc["confidence"], expires, created),
                ).fetchone()["id"]

            for ap in APPEALS:
                inc = next(i for i in INCIDENTS if i["key"] == ap["incident"])
                created = ago(inc["days_ago"] - ap["days_after_incident"])
                c.execute(
                    """INSERT INTO appeals (incident_id, user_id, text, attempt_no, status, triage,
                                            resolved_by, created_at, resolved_at)
                       VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s)""",
                    (incident_ids[ap["incident"]], user_ids[inc["username"]], ap["text"], ap["status"],
                     Jsonb(ap["triage"]), ap["resolved_by"], created,
                     created + timedelta(minutes=ap["resolved_after_min"])),
                )

        precedents = seed_precedents(c)

    print(f"seed ok: {len(CHANNELS)} channels, {len(USERS)} users, "
          f"{len(MESSAGES) + len(INCIDENTS)} messages, {len(INCIDENTS)} incidents, "
          f"{len(APPEALS)} appeals, {precedents} precedents")


def seed_precedents(c) -> int:
    try:
        from app.llm import embed  # файл B
    except ImportError as e:
        print(f"WARNING: precedents skipped, app.llm.embed is not available yet ({e})")
        return 0
    for p in PRECEDENTS:
        c.execute(
            "INSERT INTO precedents (appeal_id, summary, decision, reasoning, decided_by, embedding) "
            "VALUES (NULL, %s, %s, %s, 'seed', %s)",
            (p["summary"], p["decision"], p["reasoning"], np.array(embed(p["summary"]))),
        )
    return len(PRECEDENTS)


if __name__ == "__main__":
    seed()
