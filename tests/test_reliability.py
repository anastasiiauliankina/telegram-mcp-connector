import asyncio, os, sqlite3, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_sync import FakeGW, FakeLLM, Config, DB, Syncer  # noqa: E402


class FlakyVault:
    """Перший запис падає (диск повний), далі працює."""
    def __init__(self): self.calls, self.written = 0, []
    def write(self, title, chat_id, day, n, summary):
        self.calls += 1
        if self.calls == 1: raise OSError("disk full")
        self.written.append(day); return "x"


class StealingGW(FakeGW):
    """Імітує втрату lock (оренда спливла, інший процес забрав) наприкінці читання."""
    def __init__(self, db): super().__init__(); self.db = db
    async def fetch(self, chat_id, after_id, since_ts):
        async for m in super().fetch(chat_id, after_id, since_ts): yield m
        self.db.conn.execute("UPDATE locks SET owner='other'")


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = Config(tz="UTC", db_path=os.path.join(self.tmp, "b.db"))
        self.db = DB(self.cfg.db_path, "UTC")
        self.gw, self.llm = FakeGW(), FakeLLM()

    async def test_export_failure_is_retried_without_llm(self):
        v = FlakyVault(); s = Syncer(self.cfg, self.db, self.gw, self.llm, v)
        self.gw.add(10)
        r1 = await s.run([1])
        self.assertEqual(r1["status"], "partial"); self.assertEqual(r1["summaries_written"], 1)
        self.assertEqual(len(self.db.pending_exports(1)), 1)      # самарі є в БД, але не експортоване
        r2 = await s.run([1])
        self.assertEqual((r2["status"], r2["exported"], r2["summaries_written"]), ("ok", 1, 0))
        self.assertEqual(self.llm.calls, 1)                        # LLM вдруге НЕ викликали
        self.assertEqual(self.db.pending_exports(1), []); self.assertEqual(len(v.written), 1)
        r3 = await s.run([1]); self.assertEqual(r3["exported"], 0)  # і більше нічого не повторюється

    async def test_resummarized_day_is_exported_again(self):
        v = FlakyVault(); v.calls = 1; s = Syncer(self.cfg, self.db, self.gw, self.llm, v)
        self.gw.add(10); await s.run([1])
        self.gw.add(5, start=11); await s.run([1])
        self.assertEqual(len(v.written), 2)  # оновлене самарі знову потрапило у vault

    async def test_lock_lost_aborts_without_writing(self):
        gw = StealingGW(self.db); gw.add(10)
        s = Syncer(self.cfg, self.db, gw, self.llm)
        r = await s.run([1])
        self.assertEqual(r["status"], "partial")
        self.assertEqual(self.db.count_messages(), 0)  # heartbeat не пустив запис без lock

    def test_migration_adds_exported_ts(self):
        path = os.path.join(self.tmp, "old.db")
        c = sqlite3.connect(path)
        c.executescript("""CREATE TABLE chats(chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL,
          sync_enabled INTEGER NOT NULL DEFAULT 0, last_msg_id INTEGER NOT NULL DEFAULT 0, last_sync_ts INTEGER);
          CREATE TABLE summaries(chat_id INTEGER NOT NULL, day TEXT NOT NULL, input_hash TEXT NOT NULL,
          status TEXT NOT NULL, summary_json TEXT, model TEXT, msg_count INTEGER NOT NULL, created_ts INTEGER NOT NULL,
          PRIMARY KEY(chat_id, day));
          INSERT INTO summaries VALUES(1,'2026-11-20','h','ok','{"summary":"x"}','m',5,0);""")
        c.commit(); c.close()
        db = DB(path, "UTC")
        self.assertEqual([r["day"] for r in db.pending_exports(1)], ["2026-11-20"])


class PromptVersionTests(unittest.TestCase):
    def test_fresh_db_is_not_reported_as_invalidated_but_change_is(self):
        import tempfile as _t
        from tg_mcp.db import DB as _DB
        db = _DB(os.path.join(_t.mkdtemp(), "b.db"), "UTC")
        self.assertFalse(db.ensure_prompt_version("v1"))   # нова БД: нічого скидати
        self.assertFalse(db.ensure_prompt_version("v1"))   # та сама версія
        db.save_summary(1, "2026-11-20", "h", "ok", {"title": "t"}, "m", 3)
        self.assertTrue(db.ensure_prompt_version("v2"))    # версія змінилась -> самарі скинуто
        self.assertIsNone(db.get_summary(1, "2026-11-20"))


if __name__ == "__main__":
    unittest.main()
