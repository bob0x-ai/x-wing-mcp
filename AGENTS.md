# AGENTS.md — x-wing-mcp: X write-capable MCP server (vendored from x-wing)

## Objective

Turn the x-wing X/Twitter skill into a **stdio MCP server** exposing a **write-only
subset** of its commands. Build in this folder (currently
`/home/ubuntu/projects/x-wing-mcp`, will be relocated to `/home/ubuntu/mcp/x-wing-mcp/`).
All paths must be repo-relative — nothing may depend on the folder's absolute location.

## Background

- **x-wing skill** = X API v2 client using official `xdk` SDK, OAuth 2.0. Write-capable.
- The **live** copy is `/home/ubuntu/.hermes/skills/x-wing/` (ahead of `~/projects/x-wing`,
  which is stale). Vendor FROM the live copy.
- Vendored core: `scripts/x_client.py` (1,308 lines, argparse CLI). Contains hardened auth
  plumbing: `load_dotenv(override=True)`, `.x-wing-auth-state.json` + flock lock, 60s
  refresh cooldown, scope pre-checks, reply-policy detection.
- **17 CLI commands** exist. We only expose **7 write tools**:
  `post, thread, like, repost, follow, unfollow, dm-send`.
- Existing MCP conventions on this host (see `~/projects/x_mcp/`): `pyproject.toml` +
  `src/` layout, `mcp>=1.0` FastMCP, Hermes wrapper script pattern, stdio transport.

### X app ownership (verified)

- The `X_OAUTH2_*` tokens for the x-wing MCP live **only** in `<repo_root>/.env`
  (client_id `UURMUEFD...pjaQ`). They are no longer stored in `~/.hermes/.env`.
- `x_data`'s `official_x` provider has been re-pointed to the `mcp_official` app
  (client_id `R01OTU...`) via `~/.hermes/.env`.
- The x-wing skill is being deleted; this MCP **owns the `UURMUEFD...` app**.
  Scopes present and sufficient: `offline.access dm.read tweet.write like.write
  like.read users.read dm.write tweet.read bookmark.write follows.write`.

### Historical failure (2026-07-19)

Shared refresh chains across consumers triggered X OAuth fraud detection → full chain
revoked. Hence: **tokens live ONLY in this folder's `.env`** — never shared, never left in
`~/.hermes/.env` after migration.

## Hard constraints

1. **Do NOT modify** `/home/ubuntu/.hermes/skills/x-wing/`, `/home/ubuntu/projects/x-wing/`,
   `/home/ubuntu/projects/x_mcp/`, or `~/.hermes/.env`. Vendor copies; patch only the copies.
2. **Tokens live inside this folder** (`<repo_root>/.env`), gitignored, `chmod 600`.
3. **Single deployment** — no dev/prod separation. One folder, one `.env`.
4. **Build + test only.** No Hermes/opencode wiring, no service registration.
5. **stdio MCP transport.** stdout is protocol — the MCP path must never `print()` to stdout.
6. Write-only tools. No reads exposed.
7. Do not burn X write budget during verification (live check uses `users/me`, free).
8. Never log or echo token values.

## Target repo layout

```
<repo_root>/
├── .gitignore              # from skill (ignores .env, .x-wing-auth-state.*, __pycache__)
├── .env.example            # committed template, NO secrets
├── .env                    # real tokens, gitignored, 0600
├── pyproject.toml          # name x-wing-mcp; deps: mcp>=1.0, xdk, python-dotenv, requests
├── README.md               # tool table, provisioning, deployment notes
├── x_client.py             # VENDORED + PATCHED
├── oauth_setup.py          # VENDORED + PATCHED
├── server.py               # FastMCP stdio server, 7 write tools
└── tests/
    ├── test_posts.py
    ├── test_interactions.py
    ├── test_dms.py
    ├── test_timeline.py
    ├── test_auth_refresh.py
    ├── test_oauth_setup.py
    └── test_tools.py       # NEW: MCP schemas, handlers, isolation
```

## Milestone 1 — Vendor + scaffold

1. Copy from `/home/ubuntu/.hermes/skills/x-wing/`:
   - `scripts/x_client.py` → `<repo_root>/x_client.py`
   - `scripts/oauth_setup.py` → `<repo_root>/oauth_setup.py`
   - **All six tests** → `<repo_root>/tests/`:
     `test_posts.py`, `test_interactions.py`, `test_dms.py`, `test_timeline.py`,
     `test_auth_refresh.py`, `test_oauth_setup.py`
   - `.gitignore`, `requirements.txt` → `<repo_root>/`
2. Create `pyproject.toml`, `README.md`, `.env.example`.
3. **Patch `x_client.py`** (the copy, not the source):
   - `DEFAULT_ENV_PATH = Path.home() / ".hermes" / ".env"` →
     `Path(__file__).resolve().parent / ".env"`
   - `SKILL_DIR = Path(__file__).resolve().parent.parent` →
     `Path(__file__).resolve().parent`
   - Keep `X_WING_ENV_PATH` override functional.
4. **Patch `oauth_setup.py`**: replace hardcoded `home_dir / '.hermes' / '.env'`
   (around line 518) with env-path resolution via `X_WING_ENV_PATH` → repo `.env`.
5. **Provision `<repo_root>/.env`**: copy all `X_OAUTH2_*` and `X_*` lines from
   `~/.hermes/.env`. `chmod 600`. Do not commit.
6. `git init` + initial commit (excluding `.env` and auth-state files).
7. Vendored test files use `sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))`;
   since `x_client.py` moves to repo root, change to
   `sys.path.insert(0, str(Path(__file__).parent.parent))` in each test file.
8. Run vendored tests: `python -m pytest tests/ -x -q` — must pass.

**DoD**: files in place, tests green, and
`python -c "import x_client; print(x_client.env_path)"` → `<repo_root>/.env`.

## Milestone 2 — Library/CLI split

Problem: `get_client`, `run_auth_operation`, `_ensure_required_scopes`, and all
`cmd_*` print to stdout / call `sys.exit(1)` — fatal for an MCP stdio process.

1. Add `class XWingError(RuntimeError)`.
2. Replace every `sys.exit(1)` in the auth/refresh/plumbing path
   (`get_client`, `run_auth_operation`, `_ensure_required_scopes`, `_refresh_from_env`,
   refresh error paths) with `raise XWingError(msg)`. `main()` catches `XWingError` and
   prints to stderr + exits 1 → **CLI behavior preserved**.
3. Refactor ONLY the 7 write commands into value-returning cores:
   - `api_post(client, text, reply_to=None, quote=None, media=None) -> dict`
   - `api_thread(client, texts: list[str]) -> {"post_ids": [...]}`
   - `api_like(client, post_id) -> dict`, `api_repost(client, post_id) -> dict`
   - `api_follow(client, target_user_id) -> dict`
   - `api_unfollow(client, source_user_id, target_user_id) -> dict`
   - `api_dm_send(client, user=None, conversation=None, text) -> dict`
   Each uses `run_auth_operation` with the same `required_scopes` and reply-policy
   handling as the original `cmd_*`.
4. Existing `cmd_*` become thin print-wrappers over the `api_*` cores (so copied tests
   asserting on captured stdout still pass).
5. **Required test adaptation**: `tests/test_auth_refresh.py:154` currently asserts
   `pytest.raises(SystemExit)` for the missing-scope path. Change it to
   `pytest.raises(x_client.XWingError)`.
6. Leave the other 10 commands untouched (still CLI-only; not exposed as MCP tools).

**DoD**: CLI output identical for the 7 commands (vendored tests green);
`api_*` importable, print-free, raise `XWingError` on failure.

## Milestone 3 — MCP server (`server.py`)

- FastMCP, stdio transport, name `x-wing`.
- Before importing `x_client`: `os.environ["X_WING_ENV_PATH"] = str(REPO_ROOT / ".env")`.
- Register **7 tools** with typed schemas:
  - `post(text, reply_to=None, quote=None, media=None)`
  - `create_thread(texts: list[str])`
  - `like(post_id)`, `repost(post_id)`
  - `follow(target_user_id)`
  - `unfollow(source_user_id, target_user_id)`
  - `dm_send(user=None, conversation=None, text)`
- Handlers: call `api_*` → return dict; catch `XWingError` → raise MCP tool error with
  the real message. 403 reply-policy vs 401 auth must be distinguishable.
- No reads. No `print()` to stdout anywhere in the handler path.

**DoD**: `python -m server` starts; `tools/list` returns exactly the 7 tools.

## Milestone 4 — Tests

- Keep all six vendored/adapted test files green. `test_auth_refresh.py` and
  `test_oauth_setup.py` are critical regression coverage for the patched code — do not skip.
- Add `tests/test_tools.py`:
  - schemas present, correct names/types;
  - handler success paths with a mocked `x_client.get_client`;
  - error paths: `ReplyPolicyError`, refresh failure, missing-scope → tool error,
    not process exit;
  - **token isolation**: importing `x_client` yields `env_path == <repo_root>/.env`.

**DoD**: `python -m pytest tests/ -q` fully green.

## Milestone 5 — Verification (no write budget burned)

1. MCP handshake via stdio client: `initialize` → `tools/list` → assert 7 tools.
2. Live token check: call vendored `_validate_access_token(...)` (hits `users/me`,
   free, read-only) with the provisioned token; assert valid.
3. Record results + tool table + provisioning/deployment notes in `README.md`.

**DoD**: handshake verified, token validates, README complete.

## Deployment notes (later session)

- Folder relocates to `/home/ubuntu/mcp/x-wing-mcp/`; everything is repo-relative
  so it works unchanged.
- At deployment: delete `~/.hermes/skills/x-wing/` and migrate tokens fully out of
  `~/.hermes/.env`. This breaks `x_data`'s `read_owned_timeline` and `read_mentions`
  (official_x-only routes) — decide then whether to re-point x_data official_x at the
  `mcp_official` app in `~/.xurl`, or accept degradation. Do NOT leave a token copy behind.
- Hermes wiring (later): add `mcp_servers.x-wing` stdio entry + wrapper script
  following the `~/projects/x_mcp/scripts/x-data-mcp-hermes.sh` pattern.

## Deferred (out of scope)

x-wing repo reconciliation, deployment wiring, skill deletion, x_data re-pointing,
fresh dedicated OAuth grant, media/delete/unlike/unrepost tools, read tools,
write pacing gate.

## Reference facts

- Live vendoring source: `/home/ubuntu/.hermes/skills/x-wing/scripts/x_client.py`
- Token source: `~/.hermes/.env` (`X_OAUTH2_*` + `X_*` aliases; identical values)
- Existing MCP reference: `/home/ubuntu/projects/x_mcp/` (x-ray, read-only)
- Hermes wrapper pattern: `/home/ubuntu/projects/x_mcp/scripts/x-data-mcp-hermes.sh`
- Python: 3.11.15; `uv`/`uvx` available at `~/.local/bin`
