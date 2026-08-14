# AGENTS.md — x-wing-mcp: merged read/write X MCP server

## Objective

Maintain a single stdio MCP server for X (Twitter) that combines x-wing write tools
with x_data read tools. The server lives in `/home/neurosovereign/mcp/x-wing/`; all paths are
repo-relative so it can be relocated unchanged.

## Background

- **x-wing** = X API v2 client using the official `xdk` SDK, OAuth 2.0, write-capable.
  Vendored core: `x_client.py`.
- **x_data** = read-only X data router with multiple providers (`official_x`, `syndication`,
  `socialdata`, `getxapi`). Vendored as the `xdata/` package.
- Both codebases are vendored as copies inside this repo. The original sources in
  `/home/neurosovereign/.hermes/skills/x-wing/` and `/home/neurosovereign/projects/x_mcp/` are **not
  modified**.
- The merged server uses one X OAuth 2.0 app (`Zkh0NG1...`). Its current grant
  includes `tweet.read`, `users.read`, `like.read`, `dm.read`, and all write scopes.
  `follows.read` was added to the app and the token re-granted so `x_read_follow_graph`
  can fall back to `official_x`.

### Historical failure (2026-07-19)

Shared refresh chains across consumers triggered X OAuth fraud detection → full chain
revoked. Hence: **tokens live ONLY in this folder's `.env`** — never shared, never left in
`~/.hermes/.env` after migration.

## Hard constraints

1. **Do NOT modify** `/home/neurosovereign/.hermes/skills/x-wing/`, `/home/neurosovereign/projects/x-wing/`,
   `/home/neurosovereign/projects/x_mcp/`, or `~/.hermes/.env`. Vendor copies; patch only the copies.
2. **Tokens live inside this folder** (`<repo_root>/.env`), gitignored, `chmod 600`.
3. **Single deployment** — no dev/prod separation. One folder, one `.env`.
4. **stdio MCP transport.** stdout is protocol — the MCP path must never `print()` to stdout.
5. Do not burn X write budget during verification (live checks use `users/me`, free).
6. Never log or echo token values.

## Target repo layout

```
<repo_root>/
├── .gitignore              # ignores .env, .x-wing-auth-state.*, __pycache__
├── .env.example            # committed template, NO secrets
├── .env                    # real tokens, gitignored, 0600
├── pyproject.toml          # name x-wing-mcp
├── README.md               # tool table, provisioning, deployment notes
├── x_client.py             # vendored + patched x-wing client
├── oauth_setup.py          # vendored + patched OAuth setup
├── server.py               # FastMCP stdio server, 20 tools (7 write + 13 read)
├── xdata/                  # vendored x_data read providers/router/server
├── config/                 # provider routing configuration
└── tests/
    ├── test_posts.py
    ├── test_interactions.py
    ├── test_dms.py
    ├── test_timeline.py
    ├── test_auth_refresh.py
    ├── test_oauth_setup.py
    ├── test_tools.py              # merged server schemas + handler tests
    └── xdata/                     # vendored x_data tests
```

## Milestones (completed)

### M1 — Vendor + scaffold
- Copied `x_client.py`, `oauth_setup.py`, six x-wing tests, `.gitignore`, `requirements.txt`.
- Vendored `x_data` as `xdata/` package and `config/providers.yaml`.
- Patched env-path resolution in `x_client.py`, `oauth_setup.py`, and `xdata/providers/official_x.py`
  to use `<repo_root>/.env` via `X_WING_ENV_PATH`.
- Patched `xdata/config.py` to resolve `providers.yaml` and `x_cookies.json` repo-relative.

### M2 — Library/CLI split + x_data bug fixes
- `XWingError` class; `sys.exit(1)` replaced with exceptions in auth/refresh paths.
- 7 write commands refactored into `api_*` cores; CLI `cmd_*` wrappers preserved.
- Fixed pre-existing upstream bug in `xdata/providers/official_x.py`: `_retry_with_refresh`
  returned `(result, bool)` but callers treated the tuple as the result list, breaking
  `read_user_posts`, `search_recent`, `read_owned_timeline`, etc.
- Added `get_me` mocks to vendored tests and isolated tests from repo `.env` via
  `tests/xdata/conftest.py`.

### M3 — Merged MCP server (`server.py`)
- Single `MCPServer("x-wing")` instance.
- `load_dotenv(override=True)` on `<repo_root>/.env` before importing `xdata`.
- Existing 7 write tools + 15 read tools registered.
- `xdata.server.create_mcp_server(mcp=mcp)` re-uses the merged MCP server instance.

### M4 — Tests
- `python -m pytest tests/ -q` green (191 tests).
- Added read-tool schema checks in `tests/test_tools.py`.

### M5 — Verification
- MCP handshake lists exactly 22 tools.
- `x_data_status` reports all configured providers healthy.
- Live token check via `_validate_access_token` against `users/me`: valid.

## Deployment notes

- The repo is located at `/home/neurosovereign/mcp/x-wing/`; everything is repo-relative.
- Tokens live only in `<repo_root>/.env`. After migration, no X OAuth tokens remain in
  `~/.hermes/.env` or profile `.env` files.
- Hermes wiring:
  - Single `mcp_servers.x-wing` entry in:
    - `~/.hermes/config.yaml` (default / main profile)
    - `~/.hermes/profiles/yan-cgo/config.yaml`
    - `~/.hermes/profiles/scout/config.yaml` — read-only via `tools.exclude` on the 7 write tools
  - `x-data` entries removed.
  - Wrapper script: `x-wing-mcp-hermes.sh` (repo root, executable). It deliberately does
    **not** source `~/.hermes/.env`; the server reads tokens exclusively from
    `<repo_root>/.env`.
- Restart the Hermes gateway(s) after any `.env` or config change.

## Roadmap (deferred, out of scope)

Ranked by value-per-effort. Each item is done when: tests green, handshake lists the
expected tools, no `.env` changes beyond documented ones.

1. **Write-tool parity: media / delete / unlike / unrepost**
   CLI cores already exist (`cmd_upload_media`, `cmd_delete`, `cmd_unlike`, `cmd_unrepost`
   in `x_client.py`, tests in `tests/test_posts.py` + `tests/test_interactions.py`). Add
   `api_*` cores + MCP tool registration in `server.py` + schema tests.
   `api_post` already accepts media IDs, so `upload_media` completes the chain.
   Smallest, self-contained, highest value.

2. **Write pacing gate**
   Protect the X write budget: configurable per-window caps (e.g. hourly/daily) on the
   7 write tools, set via `.env`. Currently constraint #5 is policy, not code.

3. **OpenCode wiring**
   Mirror the Hermes wiring in OpenCode config (`opencode.jsonc`): single `x-wing` MCP
   entry, read-only filtering where needed. One entry, same wrapper script.

4. **x-wing repo reconciliation** *(resolved)*
   Upstream `bob0x-ai/x-wing` and `bob0x-ai/x-ray` repos deleted; local upstream copies
   at `/home/neurosovereign/projects/x-wing` and `/home/neurosovereign/projects/x_mcp` removed. This
   repo (`bob0x-ai/x-wing-mcp`) is now the canonical source of truth.

## Reference facts

- Canonical repo: `https://github.com/bob0x-ai/x-wing-mcp`
- Token app: x-wing app `Zkh0NG1...` (read + write scopes)
- Python: 3.11.15; `uv`/`uvx` available at `~/.local/bin`
