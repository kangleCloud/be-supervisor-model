"""应用配置加载。"""
from __future__ import annotations

import os
import os.path
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from app.core.env_loader import build_runtime_environ


DEFAULT_APP_HOST = "0.0.0.0"
DEFAULT_APP_PORT = 18880
DEFAULT_APP_LOG_LEVEL = "info"
DEFAULT_DATABASE_HOST = "127.0.0.1"
DEFAULT_DATABASE_PORT = 3306
DEFAULT_DATABASE_NAME = "supervisor_model"
DEFAULT_DATABASE_USER = "root"
DEFAULT_DATABASE_CONNECT_TIMEOUT = 5
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 480
DEFAULT_CONF_DIR = "/etc/supervisord.d"
DEFAULT_COMMAND_TIMEOUT = 30
DEFAULT_EXECUTOR_TYPE = "local"
DEFAULT_ANSIBLE_INVENTORY_PATH = "/etc/ansible/deploy_host"
DEFAULT_ANSIBLE_REMOTE_USER = "root"
DEFAULT_ALLOWED_LOG_LEVELS = {"critical", "error", "warning", "info", "debug"}

DEFAULT_HOSTS: list[dict[str, Any]] = [
    {"name": "local", "ip": "127.0.0.1", "enabled": True, "executorType": "local"},
    {"name": "web-99", "ip": "10.1.0.99", "enabled": True, "executorType": "ansible"},
    {"name": "web-104", "ip": "10.1.0.104", "enabled": True, "executorType": "ansible"},
    {"name": "web-252", "ip": "101.1.0.252", "enabled": True, "executorType": "ansible"},
]


@dataclass(frozen=True)
class AppSettings:
    """应用启动配置。"""

    host: str
    port: int
    log_level: str


@dataclass(frozen=True)
class DatabaseSettings:
    """数据库连接配置。"""

    host: str
    port: int
    database: str
    user: str
    password: str
    connect_timeout_seconds: int


@dataclass(frozen=True)
class AuthSettings:
    """认证相关配置。"""

    jwt_secret: str
    access_token_expire_minutes: int


@dataclass(frozen=True)
class SupervisorSettings:
    """Supervisor 相关配置。"""

    conf_dir: Path
    command_timeout_seconds: int


@dataclass(frozen=True)
class ExecutorSettings:
    """执行器相关配置。"""

    default_type: str
    ansible_inventory_path: Path
    ansible_remote_user: str
    ansible_timeout_seconds: int


@dataclass(frozen=True)
class HostConfig:
    """被管理主机配置。"""

    name: str
    ip: str
    enabled: bool
    executor_type: str


@dataclass(frozen=True)
class Settings:
    """应用配置聚合。"""

    repo_root: Path
    config_path: Path
    app: AppSettings
    database: DatabaseSettings
    auth: AuthSettings
    supervisor: SupervisorSettings
    executor: ExecutorSettings
    hosts: tuple[HostConfig, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    if not isinstance(payload, dict):
        raise ValueError("config.yaml 顶层结构必须是对象")
    return payload


def _get_nested(config_data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = config_data
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _optional_string(
    environ: Mapping[str, str],
    env_key: str,
    config_data: Mapping[str, Any],
    config_keys: tuple[str, ...],
    default: str,
) -> str:
    env_value = (environ.get(env_key) or "").strip()
    if env_value:
        return env_value

    config_value = _get_nested(config_data, *config_keys, default=default)
    if config_value is None:
        return default
    return str(config_value).strip() or default


def _required_string(
    environ: Mapping[str, str],
    env_key: str,
    config_data: Mapping[str, Any],
    config_keys: tuple[str, ...],
    default: str = "",
) -> str:
    value = _optional_string(environ, env_key, config_data, config_keys, default)
    if not value:
        raise ValueError(f"{env_key} 不能为空")
    return value


def _optional_int(
    environ: Mapping[str, str],
    env_key: str,
    config_data: Mapping[str, Any],
    config_keys: tuple[str, ...],
    default: int,
) -> int:
    env_value = (environ.get(env_key) or "").strip()
    raw_value: Any = env_value if env_value else _get_nested(config_data, *config_keys, default=default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{env_key} 必须是整数") from exc
    if value <= 0:
        raise ValueError(f"{env_key} 必须大于 0")
    return value


def _optional_log_level(
    environ: Mapping[str, str],
    env_key: str,
    config_data: Mapping[str, Any],
    config_keys: tuple[str, ...],
    default: str,
) -> str:
    value = _optional_string(environ, env_key, config_data, config_keys, default).lower()
    if value not in DEFAULT_ALLOWED_LOG_LEVELS:
        supported = ", ".join(sorted(DEFAULT_ALLOWED_LOG_LEVELS))
        raise ValueError(f"{env_key} 只支持 {supported}")
    return value


def _optional_path(
    environ: Mapping[str, str],
    env_key: str,
    config_data: Mapping[str, Any],
    config_keys: tuple[str, ...],
    default: str,
) -> Path:
    raw_value = _optional_string(environ, env_key, config_data, config_keys, default)
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{env_key} 必须是绝对路径")
    # 这里保留配置字面路径，只做词法归一化，避免 macOS 把 /etc 解析成 /private/etc 后污染远端路径语义。
    return Path(os.path.normpath(str(path)))


def _load_hosts(config_data: Mapping[str, Any], default_executor_type: str) -> tuple[HostConfig, ...]:
    raw_hosts = _get_nested(config_data, "hosts", default=DEFAULT_HOSTS)
    if not isinstance(raw_hosts, list):
        raise ValueError("hosts 必须是数组")

    hosts: list[HostConfig] = []
    for item in raw_hosts:
        if not isinstance(item, Mapping):
            raise ValueError("hosts 数组元素必须是对象")
        name = str(item.get("name") or "").strip()
        ip = str(item.get("ip") or "").strip()
        if not name or not ip:
            raise ValueError("host.name 和 host.ip 不能为空")
        executor_type = str(item.get("executorType") or default_executor_type).strip().lower()
        if executor_type not in {"local", "ansible"}:
            raise ValueError("host.executorType 只支持 local 或 ansible")
        hosts.append(
            HostConfig(
                name=name,
                ip=ip,
                enabled=bool(item.get("enabled", True)),
                executor_type=executor_type,
            )
        )
    return tuple(hosts)


def load_settings(environ: Optional[Mapping[str, str]] = None) -> Settings:
    """加载配置。"""
    raw_environ = environ if environ is not None else os.environ
    repo_root = _repo_root()
    runtime_environ = build_runtime_environ(raw_environ, repo_root)
    # 允许 APP_CONFIG_PATH 也来自 .env.dev / .env.prod，便于环境文件切换整套配置。
    config_path = Path((runtime_environ.get("APP_CONFIG_PATH") or "").strip() or repo_root / "config.yaml").expanduser()
    if not config_path.is_absolute():
        config_path = (repo_root / config_path).resolve()
    config_data = _load_yaml_config(config_path)

    app_settings = AppSettings(
        host=_optional_string(runtime_environ, "APP_HOST", config_data, ("app", "host"), DEFAULT_APP_HOST),
        port=_optional_int(runtime_environ, "APP_PORT", config_data, ("app", "port"), DEFAULT_APP_PORT),
        log_level=_optional_log_level(runtime_environ, "APP_LOG_LEVEL", config_data, ("app", "logLevel"), DEFAULT_APP_LOG_LEVEL),
    )

    database_settings = DatabaseSettings(
        host=_optional_string(runtime_environ, "DATABASE_HOST", config_data, ("database", "host"), DEFAULT_DATABASE_HOST),
        port=_optional_int(runtime_environ, "DATABASE_PORT", config_data, ("database", "port"), DEFAULT_DATABASE_PORT),
        database=_required_string(runtime_environ, "DATABASE_NAME", config_data, ("database", "name"), DEFAULT_DATABASE_NAME),
        user=_optional_string(runtime_environ, "DATABASE_USER", config_data, ("database", "user"), DEFAULT_DATABASE_USER),
        password=_optional_string(runtime_environ, "DATABASE_PASSWORD", config_data, ("database", "password"), ""),
        connect_timeout_seconds=_optional_int(
            runtime_environ,
            "DATABASE_CONNECT_TIMEOUT_SECONDS",
            config_data,
            ("database", "connectTimeoutSeconds"),
            DEFAULT_DATABASE_CONNECT_TIMEOUT,
        ),
    )

    auth_settings = AuthSettings(
        jwt_secret=_required_string(runtime_environ, "JWT_SECRET", config_data, ("auth", "jwtSecret")),
        access_token_expire_minutes=_optional_int(
            runtime_environ,
            "ACCESS_TOKEN_EXPIRE_MINUTES",
            config_data,
            ("auth", "accessTokenExpireMinutes"),
            DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
        ),
    )

    supervisor_settings = SupervisorSettings(
        conf_dir=_optional_path(runtime_environ, "SUPERVISOR_CONF_DIR", config_data, ("supervisor", "confDir"), DEFAULT_CONF_DIR),
        command_timeout_seconds=_optional_int(
            runtime_environ,
            "COMMAND_TIMEOUT_SECONDS",
            config_data,
            ("supervisor", "commandTimeoutSeconds"),
            DEFAULT_COMMAND_TIMEOUT,
        ),
    )

    executor_settings = ExecutorSettings(
        default_type=_optional_string(runtime_environ, "EXECUTOR_TYPE", config_data, ("executor", "type"), DEFAULT_EXECUTOR_TYPE).lower(),
        ansible_inventory_path=_optional_path(
            runtime_environ,
            "ANSIBLE_INVENTORY_PATH",
            config_data,
            ("executor", "inventoryPath"),
            DEFAULT_ANSIBLE_INVENTORY_PATH,
        ),
        ansible_remote_user=_optional_string(
            runtime_environ,
            "ANSIBLE_REMOTE_USER",
            config_data,
            ("executor", "remoteUser"),
            DEFAULT_ANSIBLE_REMOTE_USER,
        ),
        ansible_timeout_seconds=_optional_int(
            runtime_environ,
            "ANSIBLE_COMMAND_TIMEOUT_SECONDS",
            config_data,
            ("executor", "timeoutSeconds"),
            DEFAULT_COMMAND_TIMEOUT,
        ),
    )

    if executor_settings.default_type not in {"local", "ansible"}:
        raise ValueError("EXECUTOR_TYPE 只支持 local 或 ansible")

    return Settings(
        repo_root=repo_root,
        config_path=config_path,
        app=app_settings,
        database=database_settings,
        auth=auth_settings,
        supervisor=supervisor_settings,
        executor=executor_settings,
        hosts=_load_hosts(config_data, executor_settings.default_type),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取缓存后的配置。"""
    return load_settings()
