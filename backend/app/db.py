import os
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/court")


def init_schema():
    # без register_vector: розширення vector ще може не існувати
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))


def conn():
    c = psycopg.connect(DSN, row_factory=dict_row, autocommit=True)
    register_vector(c)
    return c

# використання: with db.conn() as c: rows = c.execute(sql, params).fetchall()
# ембединги передаємо як numpy.array(vec)
