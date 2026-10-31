CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS channels (
  id             SERIAL PRIMARY KEY,
  name           TEXT UNIQUE NOT NULL,
  rules          TEXT NOT NULL DEFAULT '',
  stream_context TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS users (
  id         SERIAL PRIMARY KEY,
  username   TEXT UNIQUE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
  id         SERIAL PRIMARY KEY,
  channel_id INT NOT NULL REFERENCES channels(id),
  user_id    INT NOT NULL REFERENCES users(id),
  text       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'new',   -- new | ok | flagged | acquitted | removed
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_status_idx ON messages(status);

CREATE TABLE IF NOT EXISTS incidents (
  id           SERIAL PRIMARY KEY,
  user_id      INT NOT NULL REFERENCES users(id),
  channel_id   INT NOT NULL REFERENCES channels(id),
  message_id   INT REFERENCES messages(id),
  action       TEXT NOT NULL,              -- timeout | ban
  duration_min INT,                        -- NULL для ban
  rule         TEXT NOT NULL,              -- див. Rule у schemas.py
  reason       TEXT NOT NULL,              -- одне речення для юзера
  user_summary TEXT NOT NULL,              -- 1-2 речення про поведінку юзера
  confidence   REAL NOT NULL,
  expires_at   TIMESTAMPTZ,                -- NULL = безстроково
  revoked_at   TIMESTAMPTZ,                -- заповнюється при розбані
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS appeals (
  id          SERIAL PRIMARY KEY,
  incident_id INT NOT NULL REFERENCES incidents(id),
  user_id     INT NOT NULL REFERENCES users(id),
  text        TEXT NOT NULL,
  contact     TEXT,
  attempt_no  INT NOT NULL,                -- номер спроби по цьому інциденту
  status      TEXT NOT NULL DEFAULT 'new', -- new | rejected_auto | support_queue | court_queue | resolved_upheld | resolved_overturned
  triage      JSONB,                       -- вихід TriageResult
  resolved_by TEXT,                        -- ai | support | streamer
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS court_cases (
  id             SERIAL PRIMARY KEY,
  appeal_id      INT UNIQUE NOT NULL REFERENCES appeals(id),
  status         TEXT NOT NULL DEFAULT 'preparing', -- preparing | ready | failed | confirmed
  defense        JSONB,
  prosecution    JSONB,
  judge          JSONB,
  dialogue       JSONB,
  precedent_ids  INT[] NOT NULL DEFAULT '{}',
  final_decision TEXT,                     -- uphold | overturn
  error          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS precedents (
  id         SERIAL PRIMARY KEY,
  appeal_id  INT REFERENCES appeals(id),   -- NULL для seed
  summary    TEXT NOT NULL,                -- "порушення + суть апеляції"
  decision   TEXT NOT NULL,                -- uphold | overturn
  reasoning  TEXT NOT NULL,
  decided_by TEXT NOT NULL,                -- seed | support | streamer
  embedding  vector(1024) NOT NULL,        -- gemini-embedding-001, outputDimensionality=1024
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
