#!/usr/bin/env bash
# Одна команда: bash setup.sh   — створює .venv, ставить залежності, перевіряє .env і сесію.
set -e
cd "$(dirname "$0")"
case "$(pwd)" in *:*) echo "ПОМИЛКА: у шляху до папки є двокрапка (:). Перейменуй папку без спецсимволів."; exit 1;; esac

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "ПОМИЛКА: потрібен Python 3.10+. Встанови: brew install python@3.12  (або з python.org) і запусти знову."; exit 1
fi
echo ">> Python: $($PY --version)"

rm -rf .venv
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
[ -f .env ] || { cp .env.example .env; echo ">> Створено .env — відкрий його і встав ключі."; }

echo ">> Перевірка:"
.venv/bin/python - <<'PY'
import os
import tg_mcp, telethon, anthropic
from mcp.server.fastmcp import FastMCP  # ловить несумісну версію mcp
from tg_mcp.config import Config
c = Config.from_env()
print("   залежності: OK")
print("   TG_API_ID:", "OK" if c.api_id else "ПОРОЖНІЙ (заповни .env)")
print("   TG_API_HASH:", "OK" if c.api_hash else "ПОРОЖНІЙ (заповни .env)")
print("   ANTHROPIC_API_KEY:", "OK" if c.anthropic_api_key else "ПОРОЖНІЙ (заповни .env)")
print("   Telegram-сесія:", "є" if os.path.exists(c.session_path + ".session") else "немає -> виконай: bash auth.sh")
PY
echo ">> Готово."
