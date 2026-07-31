#!/usr/bin/env bash
# Hermes MCP wrapper for the x-wing stdio MCP server.
#
# CRITICAL: Do NOT source ~/.hermes/.env. That file now contains the
# mcp_official OAuth2 credentials used by x_data. Feeding those into the
# x-wing process would recreate the shared-credential pattern that triggered
# X fraud detection and revoked the entire refresh chain on 2026-07-19.
#
# x-wing reads its tokens exclusively from <repo_root>/.env via
# X_WING_ENV_PATH, which server.py sets before importing x_client.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "${SCRIPT_DIR}"
exec python3 -m server
