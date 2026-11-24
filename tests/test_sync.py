import asyncio, os, sys, tempfile, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tg_mcp.config import Config
from tg_mcp.db import DB
from tg_mcp.models import Chat, Msg
from tg_mcp.redact import redact
from tg_mcp.sync import Syncer

DAY0 = (int(time.time()) // 86400 - 3) * 86400 + 10 * 3600  # 10:00 UTC, 3 дні тому


class FakeGW:
    def __init__(self): self.store, self.fail_after = [], None
    def add(self, n, start=1, text=None):
        for i in range(start, start + n):
            self.store.append(Msg(1, i, DAY0 + i * 60, 7, "Olena",
                                  text or f"Обговорюємо реліз {i}: потрібно оновити документацію до п'ятниці"))
    async def list_chats(self, limit): return [Chat(1, "Team", "group")]
    async def fetch(self, chat_id, after_id, since_ts):
        sent = 0
        for m in sorted(self.store, key=lambda m: m.msg_id):
            if m.msg_id <= after_id or (since_ts and m.ts < since_ts): continue
            if self.fail_after is not None and sent >= self.fail_after: raise RuntimeError("network down")
            sent += 1
            await asyncio.sleep(0)  # віддаємо керування -> запуски реально перетинаються
            yield m


class FakeLLM:
    def __init__(self): self.calls = 0
    async def summarize_day(self, title, day, lines):
        self.calls += 1
        return {"relevant": True, "title": "t", "summary": f"{len(lines)} msgs", "decisions": [],
                "action_items": [], "open_questions": [], "topics": []}


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = Config(tz="UTC", db_path=os.path.join(self.tmp, "b.db"))
        self.db = DB(self.cfg.db_path, "UTC")
        self.gw, self.llm = FakeGW(), FakeLLM()
        self.s = Syncer(self.cfg, self.db, self.gw, self.llm)

    async def test_rerun_no_duplicates_no_llm_calls(self):
        self.gw.add(20)
        r1 = await self.s.run([1]);  self.assertEqual((r1["new_messages"], r1["summaries_written"]), (20, 1))
        r2 = await self.s.run([1]);  self.assertEqual((r2["new_messages"], r2["summaries_written"]), (0, 0))
        r3 = await self.s.run([1]);  self.assertEqual(r3["new_messages"], 0)
        self.assertEqual(self.db.count_messages(), 20)
        self.assertEqual(self.llm.calls, 1)

    async def test_incremental_only_new_and_resummarize_changed_day(self):
        self.gw.add(10); await self.s.run([1])
        self.gw.add(5, start=11)
        r = await self.s.run([1])
        self.assertEqual(r["new_messages"], 5)
        self.assertEqual(self.db.count_messages(), 15)
        self.assertEqual(self.llm.calls, 2)  # день змінився -> одне повторне самарі
        self.assertEqual(self.db.cursor(1), 15)

    async def test_crash_midway_then_resume_without_gaps(self):
        self.gw.add(20)
        self.gw.fail_after = 5
        r = await self.s.run([1]);  self.assertEqual(r["status"], "partial")
        self.assertEqual(self.db.count_messages(), 5); self.assertEqual(self.db.cursor(1), 5)
        self.assertEqual(self.llm.calls, 0)  # самарі не будуємо з неповних даних
        self.gw.fail_after = None
        r = await self.s.run([1])
        self.assertEqual(self.db.count_messages(), 20)
        self.assertEqual(r["new_messages"], 15)
        ids = [m.msg_id for m in self.db.messages_for_day(1, self.db.day_of(DAY0))]
        self.assertEqual(ids, list(range(1, 21)))

    async def test_concurrent_run_is_skipped(self):
        self.gw.add(3)
        self.assertTrue(self.db.acquire_lock("sync", "other", 60))
        r = await self.s.run([1]); self.assertEqual(r["status"], "skipped")
        self.assertEqual(self.db.count_messages(), 0)

    async def test_noise_day_skips_llm(self):
        self.gw.add(10, text="ок")
        r = await self.s.run([1])
        self.assertEqual(self.llm.calls, 0); self.assertEqual(r["chats"][0]["noise_days"], 1)
        await self.s.run([1]); self.assertEqual(self.llm.calls, 0)

    async def test_gather_two_parallel_runs(self):
        self.gw.add(10)
        a, b = await asyncio.gather(self.s.run([1]), self.s.run([1]))
        self.assertEqual(sorted([a["status"], b["status"]]), ["ok", "skipped"])
        self.assertEqual(self.db.count_messages(), 10)

    def test_redact(self):
        t = redact("пароль: hunter2, пошта a.b@x.com, тел +380 67 123 45 67, key sk-abcdefghijklmnop1234")
        for leak in ("hunter2", "a.b@x.com", "123 45 67", "sk-abcdef"):
            self.assertNotIn(leak, t)


if __name__ == "__main__":
    unittest.main()
