"""Відсів шуму до LLM: економить токени і не дає моделі «вигадувати» самарі з нічого."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import Msg
from .redact import redact

_TRIVIAL = re.compile(
    r"^(ok|okay|ок|окей|так|ні|да|нет|\+1|дякую|дяк|спасибі|спасибо|thx|thanks|ty|lol|ахах\w*|хаха\w*|"
    r"привіт|привет|hi|hello|hey|gm|добрий день|доброго ранку|добрий вечір)[\s!.)(]*$", re.I)


def is_meaningful(m: Msg) -> bool:
    t = m.text.strip()
    if len(t) < 3 or not any(c.isalnum() for c in t):
        return False
    return not _TRIVIAL.match(t)


def is_noise_day(useful: list[Msg], min_messages: int, min_chars: int) -> bool:
    return len(useful) < min_messages or sum(len(m.text) for m in useful) < min_chars


def format_lines(msgs: list[Msg], tz: str, max_len: int = 1000) -> list[str]:
    z = ZoneInfo(tz)
    out = []
    for m in msgs:
        hhmm = datetime.fromtimestamp(m.ts, z).strftime("%H:%M")
        out.append(f"[{hhmm}] {m.sender}: {redact(m.text.strip())[:max_len]}")
    return out
