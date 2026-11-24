"""Ядро синхронізації: інкрементально, ідемпотентно, стійко до крашів і подвійних запусків.

Три незалежні стадії (кожна ретраїться окремо при наступному запуску):
  1) ingest   — повідомлення + курсор в одній транзакції;
  2) summarize — LLM лише для нових/змінених днів; збій не записує самарі;
  3) export   — запис у markdown vault; відстежується через summaries.exported_ts і
                повторюється, доки не вдасться (не залежить від того, чи був виклик LLM).
"""
from __future__ import annotations
import json, logging, time, uuid
from datetime import datetime, timedelta

from .config import Config
from .db import DB, LockLost, PROMPT_VERSION, input_hash
from .filtering import format_lines, is_meaningful, is_noise_day

log = logging.getLogger("tg_mcp.sync")
BATCH = 200


class Syncer:
    def __init__(self, cfg: Config, db: DB, gateway, summarizer=None, vault=None):
        self.cfg, self.db, self.gw, self.summarizer, self.vault = cfg, db, gateway, summarizer, vault
        self._owner: str | None = None
        self._ingested = 0  # скільки повідомлень поточного чату вже збережено (для звіту при частковому збої)

    def _lock(self):
        """(name, owner) для атомарної перевірки власника всередині транзакцій запису."""
        return ("sync", self._owner) if self._owner else None

    def _heartbeat(self):
        """Подовжуємо оренду lock перед кожною пачкою/днем/експортом; втрата lock = негайна зупинка.
        Це лише «рання зупинка»; остаточну гарантію дає атомарна перевірка в db (_require_lock)."""
        if self._owner and not self.db.renew_lock("sync", self._owner, self.cfg.lock_ttl):
            raise LockLost("sync lock lost (lease expired); aborting to avoid a concurrent run")

    async def run(self, chat_ids: list[int] | None = None, summarize: bool = True,
                  include_today: bool = False, trigger: str = "manual") -> dict:
        owner = uuid.uuid4().hex
        if not self.db.acquire_lock("sync", owner, self.cfg.lock_ttl):
            return {"status": "skipped", "reason": "another sync is already running"}
        self._owner = owner
        run_id = self.db.start_run(trigger)
        rep: dict = {"chats": [], "new_messages": 0, "summaries_written": 0, "exported": 0, "errors": []}
        status = "ok"
        try:
            if summarize and self.summarizer and self.db.ensure_prompt_version(PROMPT_VERSION, lock=self._lock()):
                log.info("prompt version changed -> summaries invalidated, will be rebuilt")
            for c in await self.gw.list_chats(self.cfg.dialogs_limit):
                self.db.upsert_chat(c)
            if chat_ids:
                known = {r["chat_id"] for r in self.db.get_chats()}
                unknown = [i for i in chat_ids if i not in known]
                if unknown:  # напр. чат поза dialogs_limit: не мовчимо, а повідомляємо
                    rep["unknown_chat_ids"] = unknown
                self.db.set_sync(chat_ids, True)  # явний chat_ids = свідомий opt-in
            targets = [r for r in self.db.get_chats(only_enabled=True) if not chat_ids or r["chat_id"] in chat_ids]
            if not targets:
                rep["hint"] = "Жоден чат не увімкнено: виклич enable_sync або sync_now(chat_ids=[...])"
            for r in targets:
                try:
                    cr = await self._sync_chat(r["chat_id"], r["title"], summarize, include_today)
                    rep["chats"].append(cr)
                    rep["new_messages"] += cr["new_messages"]
                    rep["summaries_written"] += cr["summaries_written"]
                    rep["exported"] += cr["exported"]
                    if cr["export_errors"]:
                        rep["errors"].append({"chat_id": r["chat_id"], "error": f"{cr['export_errors']} export(s) failed, will retry"})
                        status = "partial"
                    if cr["summary_errors"]:
                        rep["errors"].append({"chat_id": r["chat_id"], "error": f"{cr['summary_errors']} summary day(s) failed, will retry"})
                        status = "partial"
                except LockLost as e:  # lock забрали: нічого більше не пишемо і не чіпаємо інші чати
                    log.error("lock lost during chat %s: %s", r["chat_id"], e)
                    rep["new_messages"] += self._ingested
                    rep["errors"].append({"chat_id": r["chat_id"], "error": repr(e),
                                          "ingested_before_error": self._ingested})
                    rep["aborted"] = "lock lost"
                    status = "partial"
                    break
                except Exception as e:  # збій одного чату не валить решту
                    log.exception("chat %s failed", r["chat_id"])
                    rep["new_messages"] += self._ingested  # вони вже в БД і курсор їх покриває
                    rep["errors"].append({"chat_id": r["chat_id"], "error": repr(e),
                                          "ingested_before_error": self._ingested})
                    status = "partial"
        except Exception as e:
            log.exception("sync failed")
            rep["errors"].append({"error": repr(e)})
            status = "failed"
        finally:
            rep["status"] = status
            self.db.finish_run(run_id, status, rep)
            self.db.release_lock("sync", owner)
            self._owner = None
        return rep

    async def _sync_chat(self, chat_id: int, title: str, summarize: bool, include_today: bool) -> dict:
        self._ingested = 0
        cur = self.db.cursor(chat_id)
        since = int(time.time()) - self.cfg.backfill_days * 86400 if cur == 0 else None
        new, batch = 0, []

        def flush():
            nonlocal new
            if batch:
                self._heartbeat()
                n = self.db.ingest(chat_id, batch, lock=self._lock())
                new += n
                self._ingested = new
                batch.clear()

        try:
            # gateway.fetch ГАРАНТУЄ зростання msg_id => у разі збою в batch лежить безперервний префікс,
            # тому flush у finally безпечний: курсор не перестрибне пропущені повідомлення.
            async for m in self.gw.fetch(chat_id, cur, since):
                batch.append(m)
                if len(batch) >= BATCH:
                    flush()
        finally:
            flush()
        self.db.touch_sync(chat_id)

        res = {"chat_id": chat_id, "title": title, "new_messages": new, "summaries_written": 0,
               "noise_days": 0, "unchanged_days": 0, "exported": 0, "export_errors": 0, "summary_errors": 0}
        try:
            if summarize and self.summarizer:
                await self._summarize_days(chat_id, title, include_today, res)
        finally:
            self._export_pending(chat_id, title, res)  # навіть якщо LLM упав на одному з днів
        return res

    async def _summarize_days(self, chat_id: int, title: str, include_today: bool, res: dict):
        today = datetime.fromtimestamp(time.time(), self.db.tz).date()
        cutoff = (today if include_today else today - timedelta(days=1)).isoformat()
        for day in self.db.pending_days(chat_id, cutoff):
            self._heartbeat()
            msgs = self.db.messages_for_day(chat_id, day)
            h = input_hash(msgs)
            ex = self.db.get_summary(chat_id, day)
            if ex and ex["input_hash"] == h:
                res["unchanged_days"] += 1
                continue
            useful = [m for m in msgs if is_meaningful(m)]
            if is_noise_day(useful, self.cfg.min_messages, self.cfg.min_chars):
                self.db.save_summary(chat_id, day, h, "noise", None, None, len(msgs), lock=self._lock())
                res["noise_days"] += 1
                continue
            try:
                out = await self.summarizer.summarize_day(title, day, format_lines(useful, self.cfg.tz))
            except LockLost:
                raise
            except Exception as e:  # самарі НЕ записується -> наступний запуск повторить; інші дні не блокуємо
                log.error("summary %s %s failed: %r", chat_id, day, e)
                res["summary_errors"] += 1
                continue
            if out.get("relevant", True):
                self.db.save_summary(chat_id, day, h, "ok", out, self.cfg.model, len(msgs), lock=self._lock())
                res["summaries_written"] += 1
            else:
                self.db.save_summary(chat_id, day, h, "noise", None, self.cfg.model, len(msgs), lock=self._lock())
                res["noise_days"] += 1

    def _export_pending(self, chat_id: int, title: str, res: dict):
        if not self.vault:
            return
        for r in self.db.pending_exports(chat_id):
            # ownership lock перевіряється ПЕРЕД КОЖНИМ файлом (renew тільки якщо lock ще наш), а
            # позначка exported_ts пишеться з атомарною перевіркою власника; LockLost зупиняє весь запуск.
            self._heartbeat()
            try:
                self.vault.write(title, chat_id, r["day"], r["msg_count"], json.loads(r["summary_json"]))
                self.db.mark_exported(chat_id, r["day"], lock=self._lock())
                res["exported"] += 1
            except LockLost:
                raise
            except Exception as e:  # самарі в БД цілі; експорт повториться при наступному запуску
                log.error("export %s %s failed: %r", chat_id, r["day"], e)
                res["export_errors"] += 1
