"""Читабельне сховище: markdown-vault (Obsidian/Notion-імпорт сумісний). Запис атомарний і ідемпотентний."""
import json, os, re


def _slug(s: str) -> str:
    return re.sub(r"[^\w-]+", "-", s, flags=re.UNICODE).strip("-")[:40] or "chat"


def render(title: str, chat_id: int, day: str, n: int, s: dict) -> str:
    # json.dumps дає валідний YAML-рядок/список навіть для лапок і двокрапок у назвах
    L = ["---", f"chat: {json.dumps(title, ensure_ascii=False)}", f"chat_id: {chat_id}", f"date: {day}",
         f"messages: {n}", f"topics: {json.dumps(s.get('topics') or [], ensure_ascii=False)}", "---",
         f"# {s.get('title') or title} — {day}", "", s.get("summary", ""), ""]
    if s.get("decisions"):
        L += ["## Рішення"] + [f"- {x}" for x in s["decisions"]] + [""]
    if s.get("action_items"):
        L += ["## Action items"]
        for a in s["action_items"]:
            L.append(f"- [ ] {a.get('owner') or '?'}: {a.get('task')}" + (f" (термін: {a['due']})" if a.get("due") else ""))
        L.append("")
    if s.get("open_questions"):
        L += ["## Відкриті питання"] + [f"- {x}" for x in s["open_questions"]] + [""]
    return "\n".join(L)


class MarkdownVault:
    def __init__(self, root: str): self.root = root

    def _chat_dir(self, title: str, chat_id: int) -> str:
        """Папка чату прив'язана до chat_id: після перейменування чату самарі лишаються в тій самій папці."""
        suffix = f"_{abs(chat_id)}"
        if os.path.isdir(self.root):
            for name in sorted(os.listdir(self.root)):
                if name.endswith(suffix) and os.path.isdir(os.path.join(self.root, name)):
                    return os.path.join(self.root, name)
        return os.path.join(self.root, f"{_slug(title)}{suffix}")

    def write(self, title: str, chat_id: int, day: str, n: int, summary: dict) -> str:
        d = self._chat_dir(title, chat_id)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{day}.md")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(render(title, chat_id, day, n, summary))
        os.replace(tmp, path)
        return path
