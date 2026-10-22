#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then printf '먼저 bash scripts/setup.sh 를 실행하세요.\n'; exit 1; fi
if [ ! -f .env ]; then cp .env.example .env; fi
exec .venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port "${PORT:-8600}"
