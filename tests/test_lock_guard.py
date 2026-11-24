"""Додаткові перевірки втрати lock: атомарність із записом, зупинка всього запуску, жодного експорту без lock.
Існуючі тести (test_sync / test_reliability) не змінювались."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_sync import FakeGW, FakeLLM, Config, DB, Syncer  # noqa: E402
from tg_mcp.db import LockLost  # noqa: E402
from tg_mcp.models import Chat, Msg  # noqa: E402


def steal(db):
    db.conn.execute("UPDATE locks SET owner='other'")


class RecordingVault:
    def __init__(self): self.written = []
    def write(self, title, chat_id, day, n, summary): self.written.append(day); return "x"


class LockGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = Config(tz="UTC", db_path=os.path.join(self.tmp, "b.db"))
        self.db = DB(self.cfg.db_path, "UTC")

    def test_db_refuses_writes_without_ownership(self):
        self.assertTrue(self.db.acquire_lock("sync", "me", 60))
        steal(self.db)
        m = Msg(1, 1, 1_700_000_000, 7, "Olena", "text")
        with self.assertRaises(LockLost):
            self.db.ingest(1, [m], lock=("sync", "me"))
        with self.assertRaises(LockLost):
            self.db.save_summary(1, "2026-11-20", "h", "ok", {"summary": "x"}, "m", 1, lock=("sync", "me"))
        self.assertEqual(self.db.count_messages(), 0)

    async def test_ownership_checked_atomically_even_if_heartbeat_is_fooled(self):
        """Lock забрали ПІСЛЯ heartbeat, але ДО запису: запис усе одно має бути відхилений у самій транзакції."""
        class GW(FakeGW):
            def __init__(s, db): super().__init__(); s.db = db
            async def fetch(s, chat_id, after_id, since_ts):
                steal(s.db)
                async for m in super().fetch(chat_id, after_id, since_ts): yield m
        gw = GW(self.db); gw.add(10)
        self.db.renew_lock = lambda *a, **k: True   # heartbeat «не помітив» втрати
        r = await Syncer(self.cfg, self.db, gw, FakeLLM()).run([1])
        self.assertEqual(r["status"], "partial")
        self.assertEqual(self.db.count_messages(), 0)
        self.assertEqual(self.db.cursor(1), 0)

    async def test_lock_loss_stops_remaining_chats(self):
        db, fetched = self.db, []
        class TwoChats(FakeGW):
            async def list_chats(s, limit): return [Chat(1, "A", "group"), Chat(2, "B", "group")]
            async def fetch(s, chat_id, after_id, since_ts):
                fetched.append(chat_id)
                if chat_id == 1:
                    steal(db)
                for m in s.store:
                    if m.chat_id == chat_id: yield m
        gw = TwoChats(); gw.add(5)
        r = await Syncer(self.cfg, db, gw, FakeLLM()).run([1, 2])
        self.assertEqual((r["status"], r.get("aborted")), ("partial", "lock lost"))
        self.assertEqual(fetched, [1])               # чат B навіть не читали

    async def test_no_summary_or_export_after_lock_loss(self):
        db = self.db
        class StealingLLM(FakeLLM):
            async def summarize_day(s, title, day, lines):
                out = await super().summarize_day(title, day, lines)
                steal(db)                            # lock забрали під час LLM-запиту
                return out
        gw, vault = FakeGW(), RecordingVault(); gw.add(10)
        r = await Syncer(self.cfg, db, gw, StealingLLM(), vault).run([1])
        self.assertEqual(r["status"], "partial")
        self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0], 0)
        self.assertEqual(vault.written, [])
        self.assertEqual(db.count_messages(), 10)    # повідомлення були збережені до втрати lock і валідні


class ExportOwnershipTests(unittest.IsolatedAsyncioTestCase):
    """Ownership lock перевіряється перед КОЖНИМ markdown-експортом, а не раз на запуск."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = Config(tz="UTC", db_path=os.path.join(self.tmp, "b.db"))
        self.db = DB(self.cfg.db_path, "UTC")

    def _two_days(self):
        gw = FakeGW(); gw.add(10)
        for i in range(11, 21):          # другий день
            gw.store.append(Msg(1, i, gw.store[0].ts + 86400 + i * 60, 7, "Olena",
                                f"Обговорюємо реліз {i}: потрібно оновити документацію"))
        return gw

    async def test_lock_stolen_between_exports_stops_remaining_exports(self):
        db = self.db
        class StealOnFirstWrite(RecordingVault):
            def write(s, title, chat_id, day, n, summary):
                out = super().write(title, chat_id, day, n, summary)
                steal(db)                # lock забрали одразу після першого експорту
                return out
        vault = StealOnFirstWrite()
        r = await Syncer(self.cfg, db, self._two_days(), FakeLLM(), vault).run([1])
        self.assertEqual((r["status"], r.get("aborted")), ("partial", "lock lost"))
        self.assertEqual(len(vault.written), 1)          # другий день НЕ експортовано
        self.assertEqual(len(db.pending_exports(1)), 2)  # обидва лишились «не експортованими»: перший теж не позначено
        # Новий запуск (lock вільний) без LLM довозить експорт; дубль файлу — лише перезапис того самого дня
        db.conn.execute("DELETE FROM locks")
        vault2 = RecordingVault()
        llm = FakeLLM()
        r2 = await Syncer(self.cfg, db, self._two_days(), llm, vault2).run([1])
        self.assertEqual((r2["status"], r2["exported"], llm.calls), ("ok", 2, 0))
        self.assertEqual(len(vault2.written), 2)

    async def test_no_export_at_all_if_lock_lost_before_export_stage(self):
        db = self.db
        gw, vault = FakeGW(), RecordingVault(); gw.add(10)
        class StealOnSummarize(FakeLLM):
            async def summarize_day(s, title, day, lines):
                steal(db); return await super().summarize_day(title, day, lines)
        r = await Syncer(self.cfg, db, gw, StealOnSummarize(), vault).run([1])
        self.assertEqual(r["status"], "partial")
        self.assertEqual(vault.written, [])


class PromptInvalidationGuardTests(unittest.TestCase):
    def test_summaries_not_wiped_by_non_owner(self):
        import tempfile as _t
        db = DB(os.path.join(_t.mkdtemp(), "b.db"), "UTC")
        db.ensure_prompt_version("v1")
        db.save_summary(1, "2026-11-20", "h", "ok", {"title": "t"}, "m", 3)
        self.assertTrue(db.acquire_lock("sync", "other-run", 60))
        with self.assertRaises(LockLost):
            db.ensure_prompt_version("v2", lock=("sync", "me"))
        self.assertIsNotNone(db.get_summary(1, "2026-11-20"))


if __name__ == "__main__":
    unittest.main()
