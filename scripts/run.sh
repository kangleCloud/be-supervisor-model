#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

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
cd "$REPO_ROOT"

# 统一回到仓库根目录启动，避免从 scripts/ 等子目录执行时丢失 app 包导入路径。
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
