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
| `x_usage_stats` | Local usage/cost ledger summary | none |
| `x_read_own_analytics` | Owned-account analytics from the local x-analytics service (read-only, never fetches) | none (local HTTP) |

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
./x-wing-mcp-hermes.sh
```

The wrapper uses the repo-local `uv` environment and launches the server over
stdio. stdout is the MCP protocol stream - no other process should write to it.

## Analytics integration

`x_read_own_analytics` is a read-only client of the separate local
`x-analytics` service. It reads the service's stored observations over
loopback HTTP and never opens an analytics database or calls X directly.

Collection, OAuth credentials, spend limits, and freshness policy belong to
the `x-analytics` deployment. Keep this MCP tool read-only: agents must not
be able to trigger paid analytics collection.

Configure the local endpoint in `config/analytics.yaml`. x-wing accepts only
an `http` loopback origin, uses a bounded timeout, rejects redirects and
oversized/non-JSON responses, and reports a distinct `ANALYTICS_SERVICE` error
when the service is missing or unreachable.

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

## Verification

- `uv run python -m pytest tests/ -q` passes.
- An MCP handshake via the wrapper script (`initialize` → `tools/list`) returns the
  22 tools documented above.
- `x_data_status` reports the configured providers' health without exposing credentials.
- A token check against `users/me` succeeds, and `.env` plus auth-state files remain
  owner-readable only.

## Deployment notes

- This repository is relocatable: configure the MCP host with the absolute path to
  `x-wing-mcp-hermes.sh` in its checked-out repository.
- Store X credentials only in `<repo_root>/.env` (mode `0600`). Do not duplicate or
  source them from shared host, agent-profile, or global environment files.
- Register one x-wing MCP server per intended host/profile and apply that host's
  tool-access policy—for example, exclude write tools from read-only profiles.
- The wrapper reads only the repository-local `.env` and keeps stdout reserved for
  the stdio MCP protocol.

## Roadmap

See [`AGENTS.md`](AGENTS.md#roadmap-deferred-out-of-scope). Repo reconciliation is
already complete: this repo (`bob0x-ai/x-wing-mcp`) is the canonical source of truth.
