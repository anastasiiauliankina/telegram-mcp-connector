#!/usr/bin/env bash
# Демо-прогін на реальних чатах: bash scripts/demo.sh <run-name> <chat_id> [<chat_id> ...] [--include-today]
# Лог пишеться в docs/demo/<run-name>.log; назви та ID чатів автоматично маскуються (scripts/mask_log.py).
set -uo pipefail
cd "$(dirname "$0")/.."
NAME="$1"; shift
IDS=(); EXTRA=()
for a in "$@"; do
  case "$a" in
    --) ;;
    --*) EXTRA+=("$a") ;;
    *) IDS+=("$a") ;;
  esac
done
PY=.venv/bin/python
mkdir -p docs/demo
SYNC_OUT=$(mktemp); LOG=$(mktemp)
$PY -m tg_mcp.cli sync --chat-ids "${IDS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} 2>&1 | grep -v -i 'telethon' > "$SYNC_OUT"
{
  echo "### $NAME — $(date '+%Y-%m-%d %H:%M:%S')"
  echo "\$ tg-mcp-cli sync --chat-ids <${#IDS[@]} chats> ${EXTRA[*]+"${EXTRA[*]}"}"
  cat "$SYNC_OUT"
  echo "--- LLM-викликів у цьому прогоні: $(grep -c 'api.anthropic.com' "$SYNC_OUT")"
  echo "--- перевірка дублів (total має дорівнювати distinct) ---"
  $PY - data/brain.db <<'PYQ'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
t, d = c.execute("SELECT COUNT(*), COUNT(DISTINCT chat_id||':'||msg_id) FROM messages").fetchone()
print(f"messages: total={t} distinct={d} -> {'OK, дублів немає' if t == d else 'ДУБЛІ!'}")
for cid, title, cur, n in c.execute(
        "SELECT c.chat_id, c.title, c.last_msg_id, COUNT(m.msg_id) FROM chats c "
        "LEFT JOIN messages m USING(chat_id) WHERE c.sync_enabled=1 GROUP BY c.chat_id ORDER BY c.chat_id"):
    print(f"  {title} ({cid}): cursor={cur} stored={n}")
for st, n in c.execute("SELECT status, COUNT(*) FROM summaries GROUP BY status ORDER BY status"):
    print(f"summaries[{st}] = {n}")
print("summaries exported to vault =", c.execute("SELECT COUNT(*) FROM summaries WHERE exported_ts IS NOT NULL").fetchone()[0])
PYQ
} > "$LOG" 2>&1
$PY scripts/mask_log.py < "$LOG" | tee "docs/demo/$NAME.log"
rm -f "$SYNC_OUT" "$LOG"
