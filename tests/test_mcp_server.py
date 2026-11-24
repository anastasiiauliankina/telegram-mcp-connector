"""Smoke-тест MCP-шару: сервер імпортується і реєструє всі інструменти з коректними схемами.
Пропускається, якщо пакет mcp не встановлено (напр. локальний прогін лише ядра)."""
import asyncio, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from tg_mcp.server import mcp
except ImportError:  # pragma: no cover
    mcp = None

EXPECTED = {"list_chats", "get_messages", "send_message", "enable_sync", "sync_now", "sync_status", "get_summaries"}


@unittest.skipIf(mcp is None, "mcp/telethon не встановлено")
class MCPServerTests(unittest.TestCase):
    def test_all_tools_registered_with_descriptions(self):
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        self.assertEqual(set(tools), EXPECTED)
        for t in tools.values():
            self.assertTrue(t.description and t.description.strip(), t.name)
        self.assertIn("chat_id", tools["get_messages"].inputSchema["properties"])
        self.assertIn("chat_ids", tools["sync_now"].inputSchema["properties"])

    def test_send_is_disabled_by_default(self):
        from tg_mcp import server
        from tg_mcp.config import Config
        server.S["cfg"] = Config()
        try:
            with self.assertRaises(ValueError):
                asyncio.run(server.send_message(1, "hi"))
        finally:
            server.S.clear()


if __name__ == "__main__":
    unittest.main()
