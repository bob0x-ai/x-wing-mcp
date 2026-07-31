# x-wing-mcp

A write-only stdio MCP server for X (Twitter), vendored from the x-wing skill.
Exposes seven write tools backed by the official `xdk` SDK and OAuth 2.0.

## Tools

| Tool | Description | Required scope |
|------|-------------|----------------|
| `post` | Create a single post | `tweet.write` |
| `create_thread` | Create a multi-post thread | `tweet.write` |
| `like` | Like a post | `like.write` |
| `repost` | Repost a post | `tweet.write` |
| `follow` | Follow a user by ID | `follows.write` |
| `unfollow` | Unfollow a user by source/target ID | `follows.write` |
| `dm_send` | Send a direct message | `dm.write` |

No read tools are exposed.

## Provisioning

1. Ensure this repo's `.env` exists and is readable only by the owner:
   ```bash
   chmod 600 .env
   ```
2. Populate `.env` with OAuth 2.0 credentials from the X developer portal:
   ```bash
   X_OAUTH2_CLIENT_ID=...
   X_OAUTH2_CLIENT_SECRET=...
   X_OAUTH2_ACCESS_TOKEN=...
   X_OAUTH2_REFRESH_TOKEN=...
   X_OAUTH2_SCOPES="offline.access dm.read tweet.write like.write like.read users.read dm.write tweet.read bookmark.write follows.write"
   ```
   Legacy aliases (`X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_ACCESS_TOKEN`, `X_REFRESH_TOKEN`, `X_SCOPES`) are also accepted.
3. If the access token is expired, run the included OAuth setup script or use the CLI refresh path to obtain a fresh token.

## Running

```bash
python -m server
```

The server speaks MCP over stdio. stdout is the MCP protocol stream — no other process should write to it.

## Testing

```bash
python -m pytest tests/ -q
```

## Project layout

```
.
├── .env                 # real tokens, gitignored, chmod 600
├── .env.example         # committed template
├── .gitignore           # ignores .env and auth-state files
├── pyproject.toml       # x-wing-mcp package metadata
├── README.md            # this file
├── x_client.py          # vendored + patched x-wing client
├── oauth_setup.py       # vendored + patched OAuth setup
├── server.py            # FastMCP stdio server
└── tests/               # vendored + new MCP tests
```

## Verification results

- `python -c "import x_client; print(x_client.env_path)"` → `<repo_root>/.env`
- MCP handshake: `initialize` → `notifications/initialized` → `tools/list` returns exactly the 7 tools above.
- Live token check via `_validate_access_token` against `users/me`: **valid** after refresh.

## Deployment notes

- This repo is repo-relative; it can be relocated without changes.
- Tokens live only in `<repo_root>/.env`. Do not leave a copy in `~/.hermes/.env` after migration.
- At deployment, delete `~/.hermes/skills/x-wing/` and remove the old `X_OAUTH2_*` / `X_*` entries from `~/.hermes/.env`.
- Hermes wiring (later): add an `mcp_servers.x-wing` stdio entry pointing at a wrapper script in this repo, following the pattern in `~/projects/x_mcp/scripts/x-data-mcp-hermes.sh`.

## Deferred

x-wing repo reconciliation, Hermes/opencode service registration, skill deletion,
x_data re-pointing, fresh dedicated OAuth grant, media/delete/unlike/unrepost tools,
read tools, write pacing gate.
