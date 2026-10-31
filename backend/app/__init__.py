from pathlib import Path

from dotenv import load_dotenv

# backend/.env (ключ Gemma API, DATABASE_URL) підхоплюється для будь-якого запуску:
# uvicorn app.main:app, python -m app.seed, python -m app.llm, pytest
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
