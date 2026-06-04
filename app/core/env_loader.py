"""轻量环境文件加载。"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping


ENV_FILE_NAMES = {
    "dev": ".env.dev",
    "prod": ".env.prod",
}


def build_runtime_environ(environ: Mapping[str, str], repo_root: Path) -> dict[str, str]:
    """合并环境文件与进程环境变量，保证显式环境变量优先。"""
    env_file_path = resolve_env_file_path(environ, repo_root)
    if env_file_path is None:
        return dict(environ)

    merged_environ = load_env_file(env_file_path)
    # 显式传入的进程环境变量优先级最高，覆盖环境文件中的同名项。
    merged_environ.update(environ)
    return merged_environ


def resolve_env_file_path(environ: Mapping[str, str], repo_root: Path) -> Path | None:
    """根据 APP_ENV_FILE 或 APP_ENV 解析要加载的环境文件。"""
    env_file_value = (environ.get("APP_ENV_FILE") or "").strip()
    if env_file_value:
        env_file_path = Path(env_file_value).expanduser()
        if not env_file_path.is_absolute():
            raise ValueError("APP_ENV_FILE 必须是绝对路径")
        resolved_path = env_file_path.resolve()
        if not resolved_path.exists():
            raise ValueError(f"APP_ENV_FILE 指定的文件不存在: {resolved_path}")
        return resolved_path

    app_env = (environ.get("APP_ENV") or "").strip().lower()
    if not app_env:
        return None
    if app_env not in ENV_FILE_NAMES:
        raise ValueError("APP_ENV 只支持 dev 或 prod")

    env_path = (repo_root / ENV_FILE_NAMES[app_env]).resolve()
    if not env_path.exists():
        raise ValueError(f"环境文件不存在: {env_path}")
    return env_path


def load_env_file(env_file_path: Path) -> dict[str, str]:
    """解析当前项目使用的简单 KEY=VALUE 环境文件。"""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(env_file_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in raw_line:
            raise ValueError(f"环境文件格式非法: {env_file_path}:{line_number}")

        key, value = raw_line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError(f"环境变量名不能为空: {env_file_path}:{line_number}")

        normalized_value = value.strip()
        # 仅兼容最常见的整段引号包裹，避免把密码里的 # 错误当成注释截断。
        if len(normalized_value) >= 2 and normalized_value[0] == normalized_value[-1] and normalized_value[0] in {'"', "'"}:
            normalized_value = normalized_value[1:-1]
        values[normalized_key] = normalized_value
    return values
