## Agent skills

### Issue tracker

GitHub (via `gh` CLI) — remote is https://github.com/InfernYaCr/Content_Zavod; `gh` is installed and authenticated. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Code search

This repo is indexed in `jcodemunch` as `InfernYaCr/Content_Zavod`. Start code exploration there — `search_symbols` / `search_text` / `get_symbol_source` — and open files directly only for what those point at. Re-index with `index_repo(url="InfernYaCr/Content_Zavod", incremental=true)` if the index is behind `git rev-parse HEAD`.

Never read `uv.lock`, `.venv/`, or `.pytest_cache/`, and don't `grep` across the repo root without a path filter — they blow out context without adding signal.
