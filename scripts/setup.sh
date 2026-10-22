#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
if [ ! -f .env ]; then cp .env.example .env; fi
printf '설치 완료. bash scripts/run.sh 로 실행하세요.\n'
