"""Telethon (MTProto, user-акаунт). Bot API не бачить DM користувача і історію груп, тому user-сесія."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import AsyncIterator

from .config import Config
from .models import Chat, Msg


class TelethonGateway:
    def __init__(self, cfg: Config):
        from telethon import TelegramClient
        self.cfg = cfg
        os.makedirs(os.path.dirname(os.path.abspath(cfg.session_path)), exist_ok=True)
        # flood_sleep_threshold: Telethon сам переживає FloodWait <= 120с
        self.client = TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash, flood_sleep_threshold=120)

    async def connect(self):
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram-сесія не авторизована. Запусти: python -m tg_mcp.auth")
        f = self.cfg.session_path + ".session"
        if os.path.exists(f):
            os.chmod(f, 0o600)

    async def disconnect(self):
        await self.client.disconnect()

    async def list_chats(self, limit: int) -> list[Chat]:
        out = []
        async for d in self.client.iter_dialogs(limit=limit):
            if d.is_user:
                if getattr(d.entity, "bot", False):
                    continue
                t = "dm"
            else:
                t = "channel" if (d.is_channel and not d.is_group) else "group"
            out.append(Chat(d.id, d.name or str(d.id), t))
        return out

    def _to_msg(self, chat_id: int, m) -> Msg:
        s = m.sender
        name = " ".join(x for x in [getattr(s, "first_name", None), getattr(s, "last_name", None)] if x) \
            or getattr(s, "title", None) or getattr(s, "username", None) \
            or getattr(m, "post_author", None) or "unknown"
        kind = type(m.media).__name__.replace("MessageMedia", "").lower() if m.media else "text"
        return Msg(chat_id, m.id, int(m.date.timestamp()), m.sender_id, name, m.message or "",
                   m.reply_to_msg_id, kind)

    async def fetch(self, chat_id: int, after_id: int, since_ts: int | None) -> AsyncIterator[Msg]:
        """Повідомлення у ЗРОСТАЮЧОМУ порядку msg_id (контракт для курсора)."""
        kw = {"reverse": True}
        if after_id:
            kw["min_id"] = after_id
        elif since_ts:
            kw["offset_date"] = datetime.fromtimestamp(since_ts, timezone.utc)
        async for m in self.client.iter_messages(chat_id, **kw):
            if getattr(m, "action", None) is not None:  # службові повідомлення
                continue
            yield self._to_msg(chat_id, m)

    async def history(self, chat_id: int, limit: int, before_id: int | None) -> list[Msg]:
        kw = {"limit": limit}
        if before_id:
            kw["offset_id"] = before_id
        return [self._to_msg(chat_id, m) async for m in self.client.iter_messages(chat_id, **kw)
                if getattr(m, "action", None) is None]

    async def send(self, chat_id: int, text: str, reply_to: int | None = None) -> int:
        m = await self.client.send_message(chat_id, text, reply_to=reply_to)
        return m.id
