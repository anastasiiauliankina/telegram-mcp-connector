from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # корінь проєкту (editable-установка)


def _p(v: str) -> str:
    p = Path(v).expanduser()
    return str(p if p.is_absolute() else ROOT / p)


@dataclass(frozen=True)
class Config:
    api_id: int = 0
    api_hash: str = ""
    session_path: str = "data/tg"
    db_path: str = "data/brain.db"
    vault_dir: str = "data/vault"
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-5-5"
    tz: str = "Europe/Kyiv"
    sync_interval_min: int = 30
    backfill_days: int = 7
    dialogs_limit: int = 200
    min_messages: int = 3          # менше змістовних повідомлень за день -> шум, LLM не викликаємо
    min_chars: int = 80
    max_prompt_chars: int = 60000  # понад це -> map-reduce по чанках
    allow_send: bool = False
    lock_ttl: int = 1800

    @classmethod
    def from_env(cls) -> "Config":
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")  # працює незалежно від cwd (напр. запуск з Claude Desktop)
            load_dotenv()
        except ImportError:
            pass
        e = os.environ.get
        return cls(
            api_id=int(e("TG_API_ID", "0") or 0),
            api_hash=e("TG_API_HASH", ""),
            session_path=_p(e("TG_SESSION_PATH", cls.session_path)),
            db_path=_p(e("TG_DB_PATH", cls.db_path)),
            vault_dir=_p(e("TG_VAULT_DIR", cls.vault_dir)),
            anthropic_api_key=e("ANTHROPIC_API_KEY", ""),
            model=e("LLM_MODEL", cls.model),
            tz=e("TG_TIMEZONE", cls.tz),
            sync_interval_min=int(e("SYNC_INTERVAL_MIN", "30")),
            backfill_days=int(e("BACKFILL_DAYS", "7")),
            allow_send=e("TG_ALLOW_SEND", "0") == "1",
        )
