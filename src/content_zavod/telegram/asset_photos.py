"""AssetPhotos: the bot's own pictures - Инструкция slides and banners (#114) - sent from
`content_zavod/assets/guide/*.png` and cached by Telegram `file_id`.

A picture is uploaded once; the `file_id` Telegram hands back is stored in the Настройки
key-value table (`owner_settings`, key `asset_file_id:<name>`) together with a hash of the
file, so a designer's replaced PNG (same name, new bytes) is uploaded afresh instead of the old
picture being reused. A cached `file_id` Telegram refuses (another bot token, say) falls back
to an upload too.

A picture is decoration, never the message: if the file is missing or Telegram won't take the
photo, the same text goes out as a plain message, with the same buttons.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Protocol

from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from .gateway import BotClient, MessageGone

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "guide"
"""Where the PNGs live; a file's stem is its asset name (`welcome.png` -> `welcome`)."""

FILE_ID_KEY_PREFIX = "asset_file_id:"


class FileIdStore(Protocol):
    """The `owner_settings` key-value store, as far as the cache needs it."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


def asset_path(name: str, directory: Path = ASSETS_DIR) -> Path:
    return directory / f"{name}.png"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


class AssetPhotos:
    def __init__(self, bot: BotClient, store: FileIdStore, *, directory: Path = ASSETS_DIR) -> None:
        self._bot = bot
        self._store = store
        self._directory = directory

    async def send(
        self,
        chat_id: int,
        name: str,
        caption: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        *,
        text_markup: InlineKeyboardMarkup | None = None,
    ) -> int:
        """Send picture `name` with `caption`; plain text if the picture can't go - with
        `text_markup` when given (a carousel's buttons that remember it went out as text),
        else the same `reply_markup`. Returns the sent message's id either way."""
        data = self._read(name)
        if data is not None:
            digest = _digest(data)
            cached = await self._cached_file_id(name, digest)
            if cached is not None:
                try:
                    sent = await self._bot.send_photo(
                        chat_id, cached, caption=caption, reply_markup=reply_markup
                    )
                    return sent.message_id
                except Exception:
                    logger.warning("cached photo %s refused, uploading again", name, exc_info=True)
            try:
                sent = await self._bot.send_photo(
                    chat_id, self._upload(name, data), caption=caption, reply_markup=reply_markup
                )
            except Exception:
                logger.warning("could not send photo %s, sending text", name, exc_info=True)
            else:
                await self._remember(name, digest, sent.file_id)
                return sent.message_id
        return await self._bot.send_message(
            chat_id, caption, reply_markup=text_markup or reply_markup
        )

    async def edit(
        self,
        chat_id: int,
        message_id: int,
        name: str,
        caption: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        """Turn photo message `message_id` into picture `name` with `caption`. `False` (nothing
        changed) when it can't - a missing file, a text message, a deleted one; the caller
        decides what to do instead."""
        data = self._read(name)
        if data is None:
            return False
        digest = _digest(data)
        cached = await self._cached_file_id(name, digest)
        if cached is not None:
            try:
                await self._bot.edit_message_media(
                    chat_id, message_id, cached, caption=caption, reply_markup=reply_markup
                )
                return True
            except MessageGone:
                return False  # no upload can bring it back
            except Exception:
                logger.info("could not edit %s to cached photo %s", message_id, name, exc_info=True)
        try:
            file_id = await self._bot.edit_message_media(
                chat_id,
                message_id,
                self._upload(name, data),
                caption=caption,
                reply_markup=reply_markup,
            )
        except Exception:
            logger.info("could not edit %s to photo %s", message_id, name, exc_info=True)
            return False
        await self._remember(name, digest, file_id)
        return True

    # --- helpers ---

    def _read(self, name: str) -> bytes | None:
        try:
            return asset_path(name, self._directory).read_bytes()
        except OSError:
            logger.warning("picture %s is missing", name)
            return None

    @staticmethod
    def _upload(name: str, data: bytes) -> BufferedInputFile:
        return BufferedInputFile(data, filename=f"{name}.png")

    async def _cached_file_id(self, name: str, digest: str) -> str | None:
        try:
            value = await self._store.get(FILE_ID_KEY_PREFIX + name)
        except Exception:
            logger.warning("could not read the file_id of %s", name, exc_info=True)
            return None
        if value is None:
            return None
        stored_digest, _, file_id = value.partition(":")
        return file_id if stored_digest == digest and file_id else None

    async def _remember(self, name: str, digest: str, file_id: str | None) -> None:
        if not file_id:
            return
        try:
            await self._store.set(FILE_ID_KEY_PREFIX + name, f"{digest}:{file_id}")
        except Exception:
            logger.warning("could not store the file_id of %s", name, exc_info=True)
