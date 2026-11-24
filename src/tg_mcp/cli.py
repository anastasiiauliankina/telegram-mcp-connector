"""Запуск без MCP-клієнта — зручно для cron і для демо-логу (два прогони підряд)."""
import argparse, asyncio, json, logging, sys

from .app import build
from .config import Config


async def amain(a):
    cfg = Config.from_env()
    db, gw, syncer = await build(cfg)
    try:
        if a.cmd == "chats":
            for c in await gw.list_chats(cfg.dialogs_limit):
                print(f"{c.chat_id}\t{c.type}\t{c.title}")
        else:
            r = await syncer.run(a.chat_ids or None, not a.no_summary, a.include_today, trigger="cli")
            print(json.dumps(r, ensure_ascii=False, indent=2))
            print("messages_total =", db.count_messages())
    finally:
        await gw.disconnect()


def main():
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    p = argparse.ArgumentParser(prog="tg-mcp-cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chats")
    s = sub.add_parser("sync")
    s.add_argument("--chat-ids", type=int, nargs="*")
    s.add_argument("--no-summary", action="store_true")
    s.add_argument("--include-today", action="store_true")
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
