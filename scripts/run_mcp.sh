#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
mkdir -p logs

export PYTHONPATH="$PROJECT_DIR/src:$PYTHONPATH"
exec python3 -m rag_notion_kb.cli serve 2>>"$PROJECT_DIR/logs/rag_mcp_server.log"
