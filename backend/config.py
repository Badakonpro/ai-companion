import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


OLLAMA_CHAT_URL = os.getenv("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "sorc/qwen3.5-instruct-heretic")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "120"))

BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
BACKEND_RELOAD = os.getenv("BACKEND_RELOAD", "true").lower() == "true"

FRONTEND_ORIGINS = _parse_csv(os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"))
ALLOW_ALL_ORIGINS = os.getenv("ALLOW_ALL_ORIGINS", "false").lower() == "true"

STORAGE_DIR = BASE_DIR / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
SQLITE_DB_PATH = STORAGE_DIR / "main.db"

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))

# ── M4 additions ────────────────────────────────────────────────────
# Deploy mode: "local" (SQLite-only) or "server" (future: PostgreSQL + Redis)
DEPLOY_MODE = os.getenv("DEPLOY_MODE", "local")

# Security (5.3)
MAX_INPUT_LENGTH = int(os.getenv("MAX_INPUT_LENGTH", "2000"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))

# Observability (5.2)
ALERT_P95_MS = float(os.getenv("ALERT_P95_MS", "10000"))
ALERT_ERROR_RATE = float(os.getenv("ALERT_ERROR_RATE", "0.1"))

APP_VERSION = "0.5.1"


def ollama_base_url() -> str:
    parsed = urlparse(OLLAMA_CHAT_URL)
    return f"{parsed.scheme}://{parsed.netloc}"
