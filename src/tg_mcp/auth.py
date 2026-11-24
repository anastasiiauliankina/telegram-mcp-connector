"""Одноразова інтерактивна авторизація (код з Telegram + 2FA пароль, якщо є)."""
import os
from telethon import TelegramClient
from .config import Config


def main():
    cfg = Config.from_env()
    os.makedirs(os.path.dirname(os.path.abspath(cfg.session_path)), exist_ok=True)
    with TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash) as client:
        client.start()  # спитає телефон/код/пароль у терміналі
        me = client.loop.run_until_complete(client.get_me())
        print(f"OK: авторизовано як {me.first_name} (id={me.id})")
    f = cfg.session_path + ".session"
    if os.path.exists(f):
        os.chmod(f, 0o600)


if __name__ == "__main__":
    main()
