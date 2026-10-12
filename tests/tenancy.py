"""The two Пространства every DB test runs in (ADR-0015). `tests/conftest.py` creates them
once per session; cross-tenant tests act as A on B's objects (`two_workspaces` fixture)."""

from __future__ import annotations

from content_zavod.workspaces import WorkspaceId

# Far above the BIGSERIAL range a test run reaches, so `Workspaces.get_or_create` in a test
# never collides with them.
WS_A = WorkspaceId(900_001)
WS_B = WorkspaceId(900_002)
