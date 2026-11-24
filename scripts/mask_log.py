#!/usr/bin/env python3
"""Маскує реальні назви та ID чатів у демо-лозі (stdin -> stdout) стабільними псевдонімами DM-1, Group-2 ...

Псевдоніми зберігаються в data/demo_aliases.json (data/ у .gitignore), тож у run1/run2/run3 той самий чат
має той самий псевдонім. Імена беруться з таблиці chats локальної БД.
"""
import json, os, re, sqlite3, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DB = os.environ.get("TG_DB_PATH", os.path.join(ROOT, "data", "brain.db"))
ALIASES = os.path.join(ROOT, "data", "demo_aliases.json")
PREFIX = {"dm": "DM", "group": "Group", "channel": "Channel"}


def load_aliases() -> dict:
    al = json.load(open(ALIASES, encoding="utf-8")) if os.path.exists(ALIASES) else {}
    if os.path.exists(DB):
        # псевдоніми лише для чатів, що синхронізуються (у лозі з'являються тільки вони)
        rows = sqlite3.connect(DB).execute(
            "SELECT chat_id, title, type FROM chats WHERE sync_enabled=1 ORDER BY chat_id").fetchall()
        for cid, title, typ in rows:
            key = str(cid)
            if key not in al:
                p = PREFIX.get(typ, "Chat")
                n = sum(1 for v in al.values() if v["alias"].startswith(p + "-")) + 1
                al[key] = {"alias": f"{p}-{n}", "fake_id": (-1 if cid < 0 else 1) * (100000000 + len(al) + 1)}
            al[key]["title"] = title
        os.makedirs(os.path.dirname(ALIASES), exist_ok=True)
        with open(ALIASES, "w", encoding="utf-8") as f:
            json.dump(al, f, ensure_ascii=False, indent=1)
    return al


def main():
    al = load_aliases()
    subs = []
    for cid, v in al.items():
        subs.append((re.compile(rf"(?<![\d-]){re.escape(cid)}(?!\d)"), str(v["fake_id"])))
        subs.append((re.compile(rf"(?<![\d]){re.escape(cid.lstrip('-'))}(?!\d)"), str(abs(v["fake_id"]))))
        if v.get("title"):
            for form in {json.dumps(v["title"], ensure_ascii=False)[1:-1], v["title"]}:  # у JSON і як є
                subs.append((re.compile(re.escape(form)), v["alias"]))
    for line in sys.stdin:
        for rx, rep in subs:
            line = rx.sub(rep, line)
        sys.stdout.write(line)


if __name__ == "__main__":
    main()
