# AI_Courtroom

AI-модерація чату стріму + «AI-Суд» для апеляцій. Повний опис — у `ARCHITECTURE.md`.

Стек: Python 3.11+ і FastAPI, PostgreSQL + pgvector, Gemma 4 через Gemini API, фронтенд як статичний HTML, який віддає той самий FastAPI. Docker не використовуємо.

## Що потрібно

- Python 3.11 або новіший (перевірено на 3.14)
- PostgreSQL 16/17 з розширенням **pgvector**
- Ключ Gemini API (той самий ключ для Gemma 4 і для ембедингів)

## 1. PostgreSQL + pgvector

### Windows

Потрібні Visual Studio (або Build Tools) з компонентом «Desktop development with C++».
Відкрий **x64 Native Tools Command Prompt for VS від імені адміністратора**:

```bat
set "PGROOT=C:\Program Files\PostgreSQL\17"
cd %TEMP%
git clone --branch v0.8.7 https://github.com/pgvector/pgvector.git
cd pgvector
nmake /F Makefile.win
nmake /F Makefile.win install
```

Створити базу:

```bat
"C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -c "CREATE DATABASE court"
```

### macOS

```bash
brew install postgresql@17 pgvector
brew services start postgresql@17
createdb court
```

Таблиці та розширення `vector` бекенд створює сам під час старту (`schema.sql`).

## 2. Бекенд

Windows (PowerShell):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # впиши пароль Postgres і GEMINI_API_KEY
```

macOS:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # впиши пароль Postgres і GEMINI_API_KEY
```

`backend/.env` у `.gitignore`. Ключі в репозиторій не комітимо.

| Змінна | Приклад |
|---|---|
| `DATABASE_URL` | `postgresql://postgres:ПАРОЛЬ@localhost:5432/court` |
| `GEMINI_API_KEY` | ключ Gemini API |

## 3. Запуск

```bash
python -m app.llm      # smoke test AI-шару (B)
python -m app.seed     # дані для демо; повторювати перед кожним прогоном
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Сторінки:

- http://localhost:8000/chat.html
- http://localhost:8000/appeal.html
- http://localhost:8000/admin.html
- http://localhost:8000/court.html?case=1 (для OBS: `&overlay=1`)

Перевірка API: http://localhost:8000/api/health → `{"ok": true}`, документація ендпоінтів: http://localhost:8000/docs

## 4. Тести

```bash
cd backend
pytest
```

Тести не потребують ні БД, ні LLM.
