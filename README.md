# x-wing-mcp

A stdio MCP server for X (Twitter) that combines x-wing write tools with x_data read tools.
All tools share one process, one `.env`, and one official `xdk` OAuth 2.0 app.

## Tools

### Write tools

| Tool | Description | Required scope |
|------|-------------|----------------|
| `post` | Create a single post | `tweet.write` |
| `create_thread` | Create a multi-post thread | `tweet.write` |
| `like` | Like a post | `like.write` |
| `repost` | Repost a post | `tweet.write` |
| `follow` | Follow a user by ID | `follows.write` |
| `unfollow` | Unfollow a user by source/target ID | `follows.write` |
| `dm_send` | Send a direct message | `dm.write` |

### Read tools

| Tool | Description | Required scope |
|------|-------------|----------------|
| `x_fetch_urls` | Fetch exact public posts by URL/ID | `tweet.read` |
| `x_read_user_posts` | Recent posts for one user | `tweet.read` + `users.read` |
| `x_search_posts` | Search public posts | `tweet.read` |
| `x_read_owned_timeline` | Authenticated account timeline | `tweet.read` |
| `x_read_mentions` | Mentions for the authenticated account | `tweet.read` |
| `x_read_thread` | Thread/conversation from an anchor post | `tweet.read` |
| `x_read_replies` | Replies to one post | `tweet.read` |
| `x_read_quotes` | Quote posts of one post | `tweet.read` |
| `x_read_follow_graph` | Followers/following for one user | `follows.read` |
| `x_read_article` | Published X Article via wrapper tweet | `tweet.read` |
| `x_collect_posts` | One-shot bulk collection query | `tweet.read` |
| `x_data_status` | Server/provider status | none |
| `x_data_healthcheck` | Provider diagnostics | none |

Read tools use an internal provider router (`official_x`, `syndication`, `socialdata`, `getxapi`).
`max_cost_usd` is required on every read tool except status/healthcheck.

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
   X_OAUTH2_SCOPES="offline.access dm.read tweet.write like.write like.read users.read dm.write tweet.read bookmark.write follows.write follows.read"
   ```
   Legacy aliases (`X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_ACCESS_TOKEN`, `X_REFRESH_TOKEN`, `X_SCOPES`) are also accepted.
3. Optional paid/backup providers:
   ```bash
   SOCIALDATA_API_KEY=...
   GETXAPI_API_KEY=...
   ```
   Leave blank to rely on `official_x` + `syndication` only.
4. If the access token is expired, run the included OAuth setup script or use the CLI refresh path to obtain a fresh token.

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
├── server.py            # FastMCP stdio server (read + write tools)
├── xdata/               # vendored x_data read providers/router/server
├── config/              # provider routing configuration
└── tests/               # vendored + new MCP tests
```

## Verification results

- `python -c "import x_client; print(x_client.env_path)"` → `<repo_root>/.env`
- MCP handshake via the wrapper script: `initialize` → `tools/list` returns exactly the 20 tools above.
- `x_data_status` reports all configured providers healthy when tokens/keys are present.
- Live token check via `_validate_access_token` against `users/me`: **valid** after refresh
  (access token refreshed successfully; `.env` and auth-state remain 0600).

## Deployment notes

- This repo is repo-relative; it can be relocated without changes.
- Tokens live only in `<repo_root>/.env`. After migration, no X OAuth tokens should remain in
  `~/.hermes/.env` or profile `.env` files.
- The merged x-wing Hermes MCP server is wired into:
  - `~/.hermes/config.yaml` (default / main profile)
  - `~/.hermes/profiles/yan-cgo/config.yaml`
  - `~/.hermes/profiles/scout/config.yaml` — read-only via `tools.exclude` on the 7 write tools
- Wrapper script: `x-wing-mcp-hermes.sh` (repo root, executable). It deliberately
  does **not** source `~/.hermes/.env`; the server reads tokens exclusively from
  `<repo_root>/.env`.

## Deferred

x-wing repo reconciliation, OpenCode wiring, media/delete/unlike/unrepost tools, write pacing gate.
