from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Chat:
    chat_id: int
    title: str
    type: str  # dm | group | channel


@dataclass(frozen=True)
class Msg:
    chat_id: int
    msg_id: int
    ts: int  # unix seconds UTC
    sender_id: int | None
    sender: str
    text: str
    reply_to: int | None = None
    kind: str = "text"  # text | photo | voice | ...
