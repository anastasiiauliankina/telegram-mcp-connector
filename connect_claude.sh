#!/usr/bin/env bash
# Підключає сервер до Claude Desktop (macOS): bash connect_claude.sh
# Безпечно зливає з наявним конфігом, робить резервну копію, пише коректний JSON.
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
CFG="${CLAUDE_CONFIG:-$HOME/Library/Application Support/Claude/claude_desktop_config.json}"
mkdir -p "$(dirname "$CFG")"
"$ROOT/.venv/bin/python" - "$CFG" "$ROOT" <<'PY'
import json, os, shutil, sys
cfg, root = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(cfg):
    raw = open(cfg, encoding="utf-8").read()
    try:
        data = json.loads(raw) if raw.strip() else {}
        shutil.copy(cfg, cfg + ".bak")
    except Exception:
        shutil.copy(cfg, cfg + ".broken.bak")
        print(">> Старий конфіг був пошкоджений; копія: " + cfg + ".broken.bak")
        data = {}
data.setdefault("mcpServers", {})["telegram"] = {
    "command": root + "/.venv/bin/python",
    "args": ["-m", "tg_mcp.server"],
}
with open(cfg, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(">> Записано: " + cfg)
PY
echo ">> Тепер повністю закрий Claude Desktop (Cmd+Q) і відкрий знову."
