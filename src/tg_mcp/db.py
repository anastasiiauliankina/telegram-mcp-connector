"""SQLite-сховище: повідомлення, курсори синхронізації, самарі, lock.

Гарантії:
* messages PK (chat_id, msg_id) + INSERT OR IGNORE  -> дублі неможливі.
* курсор (chats.last_msg_id) рухається в ТІЙ САМІЙ транзакції, що й вставка
  повідомлень -> краш не може дати «курсор попереду даних» (пропуск) чи
  «дані без курсора» (дубль, який і так відсікся б по PK).
* summaries PK (chat_id, day) + input_hash -> самарі ідемпотентні.
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, time
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import Chat, Msg

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats(
  chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL,
  sync_enabled INTEGER NOT NULL DEFAULT 0,
  last_msg_id INTEGER NOT NULL DEFAULT 0,
  last_sync_ts INTEGER);
CREATE TABLE IF NOT EXISTS messages(
  chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL, ts INTEGER NOT NULL, day TEXT NOT NULL,
  sender_id INTEGER, sender TEXT NOT NULL, text TEXT NOT NULL, reply_to INTEGER, kind TEXT NOT NULL,
  PRIMARY KEY(chat_id, msg_id));
CREATE INDEX IF NOT EXISTS idx_messages_day ON messages(chat_id, day);
CREATE TABLE IF NOT EXISTS summaries(
  chat_id INTEGER NOT NULL, day TEXT NOT NULL, input_hash TEXT NOT NULL, status TEXT NOT NULL,
  summary_json TEXT, model TEXT, msg_count INTEGER NOT NULL, created_ts INTEGER NOT NULL, exported_ts INTEGER,
  PRIMARY KEY(chat_id, day));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sync_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_ts INTEGER, finished_ts INTEGER,
  trigger TEXT, status TEXT, report_json TEXT);
"""


class LockLost(RuntimeError):
    """Оренда lock втрачена (спливла й її забрав інший процес). Подальший запис заборонено."""


PROMPT_VERSION = "v2"  # змінюй при правках промпта: самарі будуть перегенеровані


class DB:
    def __init__(self, path: str, tz: str = "UTC"):
        self.tz = ZoneInfo(tz)
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        new = not os.path.exists(path)
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        if new:
            try: os.chmod(path, 0o600)
            except OSError: pass
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(summaries)")]
        if "exported_ts" not in cols:  # міграція старих БД
            self.conn.execute("ALTER TABLE summaries ADD COLUMN exported_ts INTEGER")

    def close(self): self.conn.close()

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _require_lock(self, lock):
        """Викликається ВСЕРЕДИНІ транзакції запису: власник lock перевіряється атомарно з самим записом
        (без вікна між «перевірили» і «записали»). lock = (name, owner) або None (без перевірки)."""
        if lock is None:
            return
        name, owner = lock
        r = self.conn.execute("SELECT owner FROM locks WHERE name=?", (name,)).fetchone()
        if r is None or r["owner"] != owner:
            raise LockLost(f"lock {name!r} is no longer owned by this run; write refused")

    def day_of(self, ts: int) -> str:
        return datetime.fromtimestamp(ts, self.tz).date().isoformat()

    def today(self) -> str:
        return self.day_of(int(time.time()))

    # ---- chats ----
    def upsert_chat(self, c: Chat):
        self.conn.execute(
            "INSERT INTO chats(chat_id,title,type) VALUES(?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, type=excluded.type",
            (c.chat_id, c.title, c.type))

    def set_sync(self, chat_ids: list[int], enabled: bool) -> int:
        with self.tx():
            n = 0
            for cid in chat_ids:
                n += self.conn.execute("UPDATE chats SET sync_enabled=? WHERE chat_id=?",
                                       (int(enabled), cid)).rowcount
        return n

    def get_chats(self, only_enabled=False) -> list[sqlite3.Row]:
        q = "SELECT * FROM chats" + (" WHERE sync_enabled=1" if only_enabled else "") + " ORDER BY title"
        return self.conn.execute(q).fetchall()

    def cursor(self, chat_id: int) -> int:
        r = self.conn.execute("SELECT last_msg_id FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        return r["last_msg_id"] if r else 0

    # ---- ingest ----
    def ingest(self, chat_id: int, msgs: list[Msg], lock: tuple[str, str] | None = None) -> int:
        """Атомарно: перевірка lock + вставка пачки + просування курсора. Повертає к-сть НОВИХ рядків."""
        if not msgs:
            return 0
        rows = [(m.chat_id, m.msg_id, m.ts, self.day_of(m.ts), m.sender_id, m.sender, m.text,
                 m.reply_to, m.kind) for m in msgs]
        with self.tx():
            self._require_lock(lock)
            before = self.conn.total_changes
            self.conn.executemany("INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,?,?,?)", rows)
            inserted = self.conn.total_changes - before
            self.conn.execute(
                "UPDATE chats SET last_msg_id=MAX(last_msg_id,?), last_sync_ts=? WHERE chat_id=?",
                (max(m.msg_id for m in msgs), int(time.time()), chat_id))
        return inserted

    def touch_sync(self, chat_id: int):
        self.conn.execute("UPDATE chats SET last_sync_ts=? WHERE chat_id=?", (int(time.time()), chat_id))

    def messages_for_day(self, chat_id: int, day: str) -> list[Msg]:
        rs = self.conn.execute("SELECT * FROM messages WHERE chat_id=? AND day=? ORDER BY msg_id",
                               (chat_id, day)).fetchall()
        return [Msg(r["chat_id"], r["msg_id"], r["ts"], r["sender_id"], r["sender"], r["text"],
                    r["reply_to"], r["kind"]) for r in rs]

    def count_messages(self, chat_id: int | None = None) -> int:
        if chat_id is None:
            return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (chat_id,)).fetchone()[0]

    # ---- summaries ----
    def pending_days(self, chat_id: int, cutoff_day: str) -> list[str]:
        """Дні без самарі або де кількість повідомлень змінилась після останнього самарі."""
        rs = self.conn.execute(
            "SELECT m.day FROM messages m LEFT JOIN summaries s "
            "ON s.chat_id=m.chat_id AND s.day=m.day WHERE m.chat_id=? AND m.day<=? "
            "GROUP BY m.day HAVING MAX(s.day) IS NULL OR MAX(s.msg_count)!=COUNT(*) ORDER BY m.day",
            (chat_id, cutoff_day)).fetchall()
        return [r["day"] for r in rs]

    def get_summary(self, chat_id: int, day: str):
        return self.conn.execute("SELECT * FROM summaries WHERE chat_id=? AND day=?", (chat_id, day)).fetchone()

    def save_summary(self, chat_id, day, input_hash, status, summary: dict | None, model, msg_count,
                     lock: tuple[str, str] | None = None):
        with self.tx():
            self._require_lock(lock)
            self.conn.execute(
                "INSERT INTO summaries(chat_id,day,input_hash,status,summary_json,model,msg_count,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,day) DO UPDATE SET "
                "input_hash=excluded.input_hash,status=excluded.status,summary_json=excluded.summary_json,"
                "model=excluded.model,msg_count=excluded.msg_count,created_ts=excluded.created_ts,exported_ts=NULL",
                (chat_id, day, input_hash, status, json.dumps(summary, ensure_ascii=False) if summary else None,
                 model, msg_count, int(time.time())))

    def pending_exports(self, chat_id: int):
        """Самарі, які є в БД, але ще не записані у vault (або перегенеровані після експорту)."""
        return self.conn.execute(
            "SELECT day, msg_count, summary_json FROM summaries "
            "WHERE chat_id=? AND status='ok' AND exported_ts IS NULL ORDER BY day", (chat_id,)).fetchall()

    def mark_exported(self, chat_id: int, day: str, lock: tuple[str, str] | None = None):
        with self.tx():
            self._require_lock(lock)
            self.conn.execute("UPDATE summaries SET exported_ts=? WHERE chat_id=? AND day=?",
                              (int(time.time()), chat_id, day))

    def list_summaries(self, chat_id=None, day=None, limit=20):
        q, a = ("SELECT s.*, c.title FROM summaries s JOIN chats c USING(chat_id) WHERE s.status='ok'", [])
        if chat_id is not None: q += " AND s.chat_id=?"; a.append(chat_id)
        if day: q += " AND s.day=?"; a.append(day)
        q += " ORDER BY s.day DESC LIMIT ?"; a.append(limit)
        return self.conn.execute(q, a).fetchall()

    def ensure_prompt_version(self, ver: str, lock: tuple[str, str] | None = None) -> bool:
        """Якщо версія промпта змінилась — скидає самарі (похідні дані), вони перебудуються з messages."""
        r = self.conn.execute("SELECT value FROM meta WHERE key='prompt_version'").fetchone()
        if r and r["value"] == ver:
            return False
        if r is None and not self.conn.execute("SELECT 1 FROM summaries LIMIT 1").fetchone():
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES('prompt_version', ?)", (ver,))
            return False  # нова БД: скидати нічого
        with self.tx():
            self._require_lock(lock)  # масове видалення — теж лише власником lock
            self.conn.execute("DELETE FROM summaries")
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES('prompt_version', ?)", (ver,))
        return True

    # ---- lock (захист від паралельних/подвійних запусків, у т.ч. cron + MCP) ----
    def acquire_lock(self, name: str, owner: str, ttl: int) -> bool:
        now = time.time()
        with self.tx():
            r = self.conn.execute("SELECT expires FROM locks WHERE name=?", (name,)).fetchone()
            if r and r["expires"] > now:
                return False
            self.conn.execute("INSERT OR REPLACE INTO locks VALUES(?,?,?)", (name, owner, now + ttl))
        return True

    def renew_lock(self, name: str, owner: str, ttl: int) -> bool:
        """Heartbeat: подовжує оренду, якщо lock ще наш. False -> lock втрачено, треба зупинитись."""
        return self.conn.execute("UPDATE locks SET expires=? WHERE name=? AND owner=?",
                                 (time.time() + ttl, name, owner)).rowcount == 1

    def release_lock(self, name: str, owner: str):
        self.conn.execute("DELETE FROM locks WHERE name=? AND owner=?", (name, owner))

    # ---- runs ----
    def start_run(self, trigger: str) -> int:
        return self.conn.execute("INSERT INTO sync_runs(started_ts,trigger,status) VALUES(?,?,'running')",
                                 (int(time.time()), trigger)).lastrowid

    def finish_run(self, run_id: int, status: str, report: dict):
        self.conn.execute("UPDATE sync_runs SET finished_ts=?,status=?,report_json=? WHERE id=?",
                          (int(time.time()), status, json.dumps(report, ensure_ascii=False), run_id))

    def last_runs(self, n=5):
        return self.conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()


def input_hash(msgs: list[Msg], version: str = PROMPT_VERSION) -> str:  # та сама версія, що й у ensure_prompt_version
    h = hashlib.sha256(version.encode())
    for m in msgs:
        h.update(f"{m.msg_id}:{hashlib.sha1(m.text.encode()).hexdigest()}\n".encode())
    return h.hexdigest()
