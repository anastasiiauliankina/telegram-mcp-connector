#!/usr/bin/env bash
# Одноразова авторизація в Telegram: bash auth.sh
cd "$(dirname "$0")"
exec .venv/bin/python -m tg_mcp.auth
