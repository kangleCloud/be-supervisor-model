#!/usr/bin/env bash
set -euo pipefail

python3.12 -m uvicorn app.main:app --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-18880}"
