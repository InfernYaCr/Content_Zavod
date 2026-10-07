"""OnboardingState: whether the Владелец still has to go through the first-run wizard (#96).

"First run" is decided from what is stored, never from a session:

- `onboarding` = `done` - finished («🚀 Запустить» or «Позже»): never offered again.
- `onboarding` = `started` - «Начать настройку» was pressed but not finished: offered again,
  even though the wizard's answers are by now stored as Настройки.
- no `onboarding` row - offered only while no Настройка is stored at all, so an install that
  configured its Ниша (or anything else) before the wizard existed is never pushed into it.

The flag lives in the same `owner_settings` key-value table as the Настройки (no migration).
Like them it is one per bot, so two Владельца share it: whoever launches first finishes it.
"""

from __future__ import annotations

from typing import Protocol

from .service import SETTING_STORE_KEYS

ONBOARDING_KEY = "onboarding"
_STARTED = "started"
_DONE = "done"


class OnboardingStore(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...

    async def set_if_changed(self, key: str, value: str) -> bool: ...


class OnboardingState:
    def __init__(self, store: OnboardingStore) -> None:
        self._store = store

    async def needed(self) -> bool:
        flag = await self._store.get(ONBOARDING_KEY)
        if flag is not None:
            return flag == _STARTED
        for key in SETTING_STORE_KEYS:
            if await self._store.get(key) is not None:
                return False
        return True

    async def begin(self) -> None:
        await self._store.set(ONBOARDING_KEY, _STARTED)

    async def finish(self) -> bool:
        """`True` only for the call that actually finished it - a double-tapped «🚀 Запустить»
        or a second Владелец launching too gets `False` and starts no second Plan."""
        return await self._store.set_if_changed(ONBOARDING_KEY, _DONE)
