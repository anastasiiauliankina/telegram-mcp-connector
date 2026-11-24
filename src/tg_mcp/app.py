from __future__ import annotations
from .config import Config
from .db import DB
from .export import MarkdownVault
from .sync import Syncer


async def build(cfg: Config):
    from .telegram_client import TelethonGateway
    from .summarizer import LLMSummarizer
    db = DB(cfg.db_path, cfg.tz)
    gw = TelethonGateway(cfg)
    await gw.connect()
    llm = LLMSummarizer(cfg.anthropic_api_key, cfg.model, cfg.max_prompt_chars) if cfg.anthropic_api_key else None
    return db, gw, Syncer(cfg, db, gw, llm, MarkdownVault(cfg.vault_dir))
