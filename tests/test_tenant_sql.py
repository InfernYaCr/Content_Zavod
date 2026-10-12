"""Every SQL statement on a table with `workspace_id` must be scoped by it (ADR-0015).

Walks the string literals of every module under `src/content_zavod` (migrations aside): a
`SELECT`/`UPDATE`/`DELETE` touching a tenant table without `workspace_id = $n`, an
`INSERT ... VALUES` without the column, or a table name interpolated into an f-string fails
this test - that is how a query reading another Пространство's rows gets caught in CI, not
in production. The only statements allowed to work across Workspaces are listed in
`CROSS_WORKSPACE` by function, each with its reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "content_zavod"

TENANT_TABLES = (
    "plans",
    "plan_items",
    "articles",
    "article_versions",
    "generation_steps",
    "topic_research",
    "jobs",
    "pending_inputs",
    "schedule_settings",
    "workspace_settings",
    "limit_charges",
)

CROSS_WORKSPACE = {
    # The worker side of the queue: it acts on a Job already claimed by id + lease token
    # and hands the Job's own workspace_id on (ClaimedJob/JobResult).
    "JobQueue.claim_next": "claims the next Job of any Workspace",
    "JobQueue.heartbeat": "the claimed Job, by id + lease_token",
    "JobQueue.complete": "the claimed Job, by id + lease_token",
    "JobQueue.fail": "the claimed Job, by id + lease_token",
    "JobQueue.recover_stuck": "requeues abandoned Jobs of every Workspace",
    "JobQueue.claim_notification": "claims the next finished Job of any Workspace",
    "JobQueue.mark_notified": "the claimed notification, by Job id",
    "JobQueue.reschedule_notification": "the claimed notification, by Job id",
    # The Расписание tick (ADR-0015): the one place that picks due Workspaces among all.
    "schedulable_workspaces": "the Расписание tick picks due Workspaces across all of them",
}

_STATEMENT = re.compile(r"\b(SELECT|UPDATE|DELETE|INSERT)\b")
_TENANT_TABLE = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(" + "|".join(TENANT_TABLES) + r")\b")
# A read or write must filter by a bound workspace_id, not just name the column (in a SELECT
# list, a RETURNING, or a JOIN between two tenant tables). Only INSERT ... VALUES, whose
# column list carries it, gets away with a bare mention.
_FILTERED = re.compile(r"workspace_id\s*=\s*(?:\$\d|ANY\b)")
_INSERT_VALUES = re.compile(r"\bINSERT\b[\s\S]*\bVALUES\b")
# A table name interpolated into SQL can't be checked at all.
_DYNAMIC_TABLE = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s*$")


def _scoped(sql: str) -> bool:
    if _INSERT_VALUES.search(sql):
        return "workspace_id" in sql
    return _FILTERED.search(sql) is not None


def violations(source: str, module: str) -> tuple[list[str], set[str]]:
    """(statements without workspace_id, CROSS_WORKSPACE functions that hold tenant SQL)."""
    found: list[str] = []
    exempt_used: set[str] = set()

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, [*scope, child.name])
                continue
            name = ".".join(scope)
            if isinstance(child, ast.JoinedStr):
                for part, after in zip(child.values, child.values[1:], strict=False):
                    if (
                        isinstance(part, ast.Constant)
                        and isinstance(after, ast.FormattedValue)
                        and _DYNAMIC_TABLE.search(part.value)
                    ):
                        found.append(f"{module}:{child.lineno} {name}: interpolated table")
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                sql = child.value
                table = _TENANT_TABLE.search(sql)
                if table and _STATEMENT.search(sql):
                    if name in CROSS_WORKSPACE:
                        exempt_used.add(name)
                    elif not _scoped(sql):
                        found.append(f"{module}:{child.lineno} {name}: {table.group(1)}")
            visit(child, scope)

    visit(ast.parse(source), [])
    return found, exempt_used


def test_tenant_sql_always_names_workspace_id() -> None:
    found: list[str] = []
    exempt_used: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        module = str(path.relative_to(SRC))
        module_found, module_exempt = violations(path.read_text(encoding="utf-8"), module)
        found += module_found
        exempt_used |= module_exempt

    assert found == [], "SQL on a tenant table without workspace_id:\n" + "\n".join(found)
    # A stale exemption would silently allow a new cross-Workspace query under its name.
    assert exempt_used == set(CROSS_WORKSPACE)


def test_the_check_catches_a_query_without_workspace_id() -> None:
    source = """
class Plan:
    async def get(self, plan_id):
        return await self._pool.fetchrow("SELECT id FROM plans WHERE id = $1", plan_id)

    async def get_scoped(self, workspace_id, plan_id):
        return await self._pool.fetchrow(
            "SELECT id FROM plans WHERE id = $1 AND workspace_id = $2", plan_id, workspace_id
        )

    async def owner_of(self, plan_id):
        return await self._pool.fetchval("SELECT workspace_id FROM plans WHERE id = $1", plan_id)

    async def add(self, workspace_id, plan_id):
        await self._pool.execute(
            "INSERT INTO plans (id, workspace_id) VALUES ($1, $2)", plan_id, workspace_id
        )

    async def count(self, table):
        return await self._pool.fetchval(f"SELECT count(*) FROM {table} WHERE workspace_id = $1")
"""
    found, _ = violations(source, "domain/plan.py")

    assert found == [
        "domain/plan.py:4 Plan.get: plans",
        "domain/plan.py:12 Plan.owner_of: plans",
        "domain/plan.py:20 Plan.count: interpolated table",
    ]
