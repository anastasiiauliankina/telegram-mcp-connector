#!/usr/bin/env python3
"""Офлайн-демо ядра синхронізації БЕЗ Telegram і без ключів (фейковий gateway + фейковий LLM).

Показує на реальному коді Syncer/DB: перший прогін, повторний (0 нових), дозапис нових повідомлень,
краш посеред читання з відновленням без пропусків, паралельний запуск (skipped) і перевірку дублів у БД.
Це ДЕТЕРМІНОВАНА перевірка логіки, а не заміна демо на реальних чатах (для них — scripts/demo.sh).

Запуск:  python3 scripts/demo_offline.py | tee docs/demo/offline_demo.log
"""
import asyncio, json, logging, os, sys, tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tg_mcp.config import Config          # noqa: E402
from tg_mcp.db import DB                  # noqa: E402
from tg_mcp.export import MarkdownVault   # noqa: E402
from tg_mcp.models import Chat, Msg      # noqa: E402
from tg_mcp.sync import Syncer            # noqa: E402

BASE = int(datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc).timestamp())


class FakeGateway:
    def __init__(self):
        self.store: dict[int, list[Msg]] = {1: [], 2: []}
        self.fail_after = None

    def add(self, chat_id: int, n: int, day_offset: int):
        start = len(self.store[chat_id]) + 1
        for i in range(start, start + n):
            self.store[chat_id].append(Msg(
                chat_id, i, BASE + day_offset * 86400 + i * 60, 7, "Olena",
                f"Обговорюємо реліз {i}: потрібно оновити документацію до п'ятниці"))

    async def list_chats(self, limit):
        await asyncio.sleep(0.05)  # реальний мережевий виклик: інший запуск встигає спробувати взяти lock
        return [Chat(1, "DM-1", "dm"), Chat(2, "Group-1", "group")]

    async def fetch(self, chat_id, after_id, since_ts):
        sent = 0
        for m in self.store[chat_id]:
            if m.msg_id <= after_id or (since_ts and m.ts < since_ts):
                continue
            if self.fail_after is not None and sent >= self.fail_after:
                raise RuntimeError("network down (simulated)")
            sent += 1
            await asyncio.sleep(0)
            yield m


class FakeLLM:
    def __init__(self): self.calls = 0

    async def summarize_day(self, title, day, lines):
        self.calls += 1
        return {"relevant": True, "title": f"{title}: підсумок", "summary": f"{len(lines)} змістовних повідомлень",
                "decisions": [], "action_items": [], "open_questions": [], "topics": ["demo"]}


def show(name, rep, db, llm):
    print(f"\n### {name}")
    print(json.dumps({k: rep.get(k) for k in ("status", "reason", "new_messages", "summaries_written", "exported")},
                     ensure_ascii=False))
    total, distinct = db.conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT chat_id||':'||msg_id) FROM messages").fetchone()
    cursors = {r["chat_id"]: r["last_msg_id"] for r in db.get_chats(only_enabled=True)}
    print(f"messages total={total} distinct={distinct} | cursors={cursors} | llm_calls_total={llm.calls}")


async def main():
    logging.disable(logging.CRITICAL)  # очікувані помилки симуляції не засмічують лог
    tmp = tempfile.mkdtemp()
    cfg = Config(tz="UTC", db_path=os.path.join(tmp, "brain.db"), backfill_days=3650)
    db, gw, llm = DB(cfg.db_path, "UTC"), FakeGateway(), FakeLLM()
    s = Syncer(cfg, db, gw, llm, MarkdownVault(os.path.join(tmp, "vault")))
    print("SIMULATED RUN: фейковий Telegram + фейковий LLM, реальний код sync/db/export")

    gw.add(1, 12, 0); gw.add(2, 30, 0); gw.add(2, 20, 1)
    show("run1 — перший прогін (2 чати, 3 дні)", await s.run([1, 2]), db, llm)
    show("run2 — повторний прогін (очікуємо 0 нових, 0 нових самарі)", await s.run([1, 2]), db, llm)

    gw.add(2, 7, 1)
    show("run3 — у чаті з'явилось рівно 7 нових повідомлень", await s.run([1, 2]), db, llm)

    gw.add(1, 15, 2); gw.fail_after = 6
    show("run4 — мережа впала посеред читання (partial)", await s.run([1, 2]), db, llm)
    gw.fail_after = None
    show("run5 — відновлення: дочитує без пропусків і дублів", await s.run([1, 2]), db, llm)
    show("run6 — ще один повторний прогін (0 нових)", await s.run([1, 2]), db, llm)

    a, b = await asyncio.gather(s.run([1, 2]), s.run([1, 2]))
    print("\n### run7 — два запуски одночасно (cron + MCP)")
    print("statuses:", sorted([a["status"], b["status"]]), "(одночасний запуск пропущено, а не виконано вдруге)")
    ids = [m.msg_id for m in db.messages_for_day(2, "2026-11-21")]
    print("перевірка безперервності msg_id за день у чаті 2:", ids == sorted(set(ids)) and ids == list(range(ids[0], ids[-1] + 1)))


asyncio.run(main())
