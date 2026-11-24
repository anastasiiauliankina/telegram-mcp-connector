"""Маскування секретів/PII ПЕРЕД відправкою тексту в LLM.

Це regex-евристика, а не гарантія. Для зменшення хибних спрацювань:
* «картка» маскується лише якщо номер проходить перевірку Луна (не ID замовлень/таймстемпи);
* «телефон» не чіпає дати (24.11.2026, 2026-11-24) та версії (1.2.3.4.5.6.7.8).
"""
import re

_PATTERNS = [
    ("tg_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("api_key", re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
                           r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,})\b")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{8,}\d(?![\w])")
_DATE_PREFIX = re.compile(
    r"^(?:\d{4}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|[12]\d|3[01])[-./](?:0?[1-9]|1[0-2])[-./](?:\d{4}|\d{2}))(?!\d)")
_ASSIGN = re.compile(r"(?i)\b(pass(?:word)?|пароль|pwd|token|secret|api[_-]?key)\b(\s*[:=\-]\s*)(\S+)")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _card_sub(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return "[REDACTED:card]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)


def _phone_sub(m: re.Match) -> str:
    s = m.group(0)
    digits = re.sub(r"\D", "", s)
    if not 9 <= len(digits) <= 15:
        return s
    if _DATE_PREFIX.match(s):                      # дата, а не телефон
        return s
    if "." in s and not s.startswith("+") and re.fullmatch(r"\d{1,3}(?:\.\d{1,3})+", s):  # версія 1.2.3.4...
        return s
    return "[REDACTED:phone]"


def redact(text: str) -> str:
    text = _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:secret]", text)
    for name, rx in _PATTERNS:
        text = rx.sub(f"[REDACTED:{name}]", text)
    text = _CARD.sub(_card_sub, text)
    return _PHONE.sub(_phone_sub, text)
