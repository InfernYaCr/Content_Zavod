"""Shared process-lifecycle glue for both entrypoints (bot, worker)."""

from __future__ import annotations

import asyncio
import signal


def register_shutdown(stop: asyncio.Event) -> None:
    """Set `stop` on SIGINT/SIGTERM so the process's main loops exit cleanly."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # add_signal_handler is unavailable on Windows event loops; SIGINT still
            # raises KeyboardInterrupt, which each entrypoint's __main__ turns into a clean stop.
            pass
