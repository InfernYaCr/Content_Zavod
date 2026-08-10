"""Launches the Telegram bot process. See src/content_zavod/entrypoints/bot.py."""

import asyncio

from content_zavod.entrypoints.bot import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
