"""Launches the Job Queue worker process. See src/content_zavod/entrypoints/worker.py."""

import asyncio

from content_zavod.entrypoints.worker import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
