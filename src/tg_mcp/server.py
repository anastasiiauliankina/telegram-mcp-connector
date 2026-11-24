"""MCP-сервер (stdio). ВАЖЛИВО: у stdio-режимі stdout зарезервований під протокол — логи лише в stderr."""
from __future__ import annotations
import asyncio, contextlib, json, logging, sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from .app import build
from .config import Config

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("tg_mcp")
S: dict = {}


STARTUP_DELAY_S = 15  # перший прогін одразу після старту: доганяємо все, що прийшло, поки сервер був вимкнений


async def _periodic(minutes: int):
    delay = STARTUP_DELAY_S
    while True:
        await asyncio.sleep(delay)
        delay = minutes * 60
        try:
            r = await S["syncer"].run(trigger="periodic")
            log.info("periodic sync: %s new=%s", r.get("status"), r.get("new_messages"))
        except Exception:
            log.exception("periodic sync crashed")


@asynccontextmanager
async def lifespan(_server):
    cfg = Config.from_env()
    db, gw, syncer = await build(cfg)
    S.update(cfg=cfg, db=db, gw=gw, syncer=syncer)
    task = asyncio.create_task(_periodic(cfg.sync_interval_min)) if cfg.sync_interval_min > 0 else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await gw.disconnect()
        db.close()


mcp = FastMCP("telegram-second-brain", lifespan=lifespan)


def _m(m):
    return {"id": m.msg_id, "time": datetime.fromtimestamp(m.ts, timezone.utc).isoformat(),
            "sender": m.sender, "text": m.text, "kind": m.kind, "reply_to": m.reply_to}


@mcp.tool()
async def list_chats(query: str = "", limit: int = 50) -> list[dict]:
    """Список чатів Telegram (DM, групи, канали) і чи увімкнена їх синхронізація."""
    chats = await S["gw"].list_chats(S["cfg"].dialogs_limit)
    for c in chats:
        S["db"].upsert_chat(c)
    enabled = {r["chat_id"] for r in S["db"].get_chats(only_enabled=True)}
    q = query.lower()
    return [{"chat_id": c.chat_id, "title": c.title, "type": c.type, "sync_enabled": c.chat_id in enabled}
            for c in chats if q in c.title.lower()][:limit]


@mcp.tool()
async def get_messages(chat_id: int, limit: int = 30, before_id: int | None = None) -> list[dict]:
    """Останні повідомлення чату напряму з Telegram (limit<=100). Це «сирий» перегляд, не синхронізація."""
    msgs = await S["gw"].history(chat_id, min(max(limit, 1), 100), before_id)
    return [_m(m) for m in msgs]


@mcp.tool()
async def send_message(chat_id: int, text: str, reply_to: int | None = None) -> dict:
    """Надіслати повідомлення. Вимкнено за замовчуванням (TG_ALLOW_SEND=1). Надсилай лише за явним проханням користувача."""
    if not S["cfg"].allow_send:
        raise ValueError("Відправку вимкнено. Встанови TG_ALLOW_SEND=1 у середовищі сервера.")
    if not text.strip() or len(text) > 4096:
        raise ValueError("text має бути 1..4096 символів")
    return {"sent": True, "message_id": await S["gw"].send(chat_id, text, reply_to)}


@mcp.tool()
async def enable_sync(chat_ids: list[int], enabled: bool = True) -> dict:
    """Увімкнути/вимкнути чати для синхронізації (allowlist: за замовчуванням нічого не синхронізується)."""
    return {"updated": S["db"].set_sync(chat_ids, enabled)}


@mcp.tool()
async def sync_now(chat_ids: list[int] | None = None, summarize: bool = True, include_today: bool = False) -> dict:
    """Синхронізація за командою: тягне ТІЛЬКИ нові повідомлення (курсор на чат) і будує денні самарі.
    Безпечна до повторних/паралельних викликів. chat_ids вмикає ці чати в allowlist."""
    return await S["syncer"].run(chat_ids, summarize, include_today, trigger="manual")


@mcp.tool()
async def sync_status() -> dict:
    """Стан: курсори чатів, кількість повідомлень, останні запуски."""
    db = S["db"]
    return {"messages_total": db.count_messages(),
            "chats": [{"chat_id": r["chat_id"], "title": r["title"], "last_msg_id": r["last_msg_id"],
                       "last_sync_ts": r["last_sync_ts"]} for r in db.get_chats(only_enabled=True)],
            "last_runs": [{"id": r["id"], "status": r["status"], "trigger": r["trigger"],
                           "report": json.loads(r["report_json"] or "{}")} for r in db.last_runs(3)]}


@mcp.tool()
async def get_summaries(chat_id: int | None = None, day: str | None = None, limit: int = 20) -> list[dict]:
    """Готові денні самарі (YYYY-MM-DD) з бази знань."""
    return [{"chat": r["title"], "chat_id": r["chat_id"], "day": r["day"], "messages": r["msg_count"],
             **json.loads(r["summary_json"])} for r in S["db"].list_summaries(chat_id, day, limit)]


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
