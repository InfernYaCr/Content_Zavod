"""JoinRequestFlow: the /start "Запросить доступ" -> Owner approve/decline pipeline.

Broadcasts every request to all current Owners at once (an inactive Owner
must not silently swallow a request). Only the resolver whose call actually
transitions the request out of `pending` grants access and notifies the
requester - `JoinRequests.resolve` is idempotent, so a second Owner's tap
just re-edits the (already-edited) broadcast messages, a safe no-op.
Approval always grants `content_manager` - granting `owner` stays a manual
DB operation, never available through this UI.
"""

from __future__ import annotations

from typing import Protocol

from ..access import JoinRequestBroadcast, JoinRequestView, Role
from .gateway import TelegramGateway, build_join_request_keyboard


class JoinRequestOperations(Protocol):
    async def create(self, telegram_id: int, username: str | None) -> int: ...

    async def get(self, join_request_id: int) -> JoinRequestView: ...

    async def record_broadcast(
        self, join_request_id: int, owner_telegram_id: int, chat_id: int, message_id: int
    ) -> None: ...

    async def broadcasts_for(self, join_request_id: int) -> list[JoinRequestBroadcast]: ...

    async def resolve(self, join_request_id: int, *, approved: bool, resolved_by: int) -> JoinRequestView: ...


class MembershipOperations(Protocol):
    async def list_by_role(self, role: Role) -> list[int]: ...

    async def add_member(self, telegram_id: int, role: Role) -> None: ...


class JoinRequestFlow:
    def __init__(
        self, requests: JoinRequestOperations, membership: MembershipOperations, gateway: TelegramGateway
    ) -> None:
        self._requests = requests
        self._membership = membership
        self._gateway = gateway

    async def request_access(self, telegram_id: int, username: str | None) -> None:
        request_id = await self._requests.create(telegram_id, username)
        owner_ids = await self._membership.list_by_role("owner")
        who = f"@{username}" if username else str(telegram_id)
        text = f"Заявка на доступ от {who} (id {telegram_id})."
        keyboard = build_join_request_keyboard(request_id)
        for owner_id in owner_ids:
            # A private chat with the bot has chat_id == the user's own telegram_id.
            message_id = await self._gateway.send_message(owner_id, text, reply_markup=keyboard)
            await self._requests.record_broadcast(request_id, owner_id, owner_id, message_id)

    async def handle_approve(
        self, resolver_id: int, resolver_name: str, join_request_id: int
    ) -> JoinRequestView | None:
        return await self._resolve(resolver_id, resolver_name, join_request_id, approved=True)

    async def handle_decline(
        self, resolver_id: int, resolver_name: str, join_request_id: int
    ) -> JoinRequestView | None:
        return await self._resolve(resolver_id, resolver_name, join_request_id, approved=False)

    async def _resolve(
        self, resolver_id: int, resolver_name: str, join_request_id: int, *, approved: bool
    ) -> JoinRequestView | None:
        view = await self._requests.resolve(join_request_id, approved=approved, resolved_by=resolver_id)
        if not view.resolved_now:
            return None  # another Owner won the atomic pending -> resolved transition
        if view.status == "approved":
            await self._membership.add_member(view.telegram_id, "content_manager")
            await self._gateway.send_message(
                view.telegram_id,
                "Доступ выдан. Нажмите /start ещё раз, чтобы увидеть доступные команды.",
            )

        verdict = "одобрена" if view.status == "approved" else "отклонена"
        resolved_text = f"Заявка от {view.telegram_id}: {verdict} пользователем {resolver_name}."
        for broadcast in await self._requests.broadcasts_for(join_request_id):
            await self._gateway.edit_notice(broadcast.chat_id, broadcast.message_id, resolved_text)
        return view
