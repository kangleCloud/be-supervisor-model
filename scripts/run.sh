#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法: ./scripts/run.sh <dev|prod>" >&2
  exit 1
fi

app_env="$1"
case "$app_env" in
  dev|prod)
    ;;
  *)
    echo "环境参数只支持 dev 或 prod" >&2
    exit 1
    ;;
esac

export APP_ENV="$app_env"

# 统一复用 Python 侧配置加载逻辑，避免 shell 再单独解析 .env 与 host/port。
python3.12 - <<'PY'
from app.core.config import get_settings
from app.main import app
import uvicorn

settings = get_settings()
uvicorn.run(
    app,
    host=settings.app.host,
    port=settings.app.port,
    log_level=settings.app.log_level,
)
PY
