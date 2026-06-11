"""Tortoise ORM 启动与事务封装。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus

import yaml
from tortoise import Tortoise
from tortoise.transactions import in_transaction as tortoise_in_transaction

from app.core.config import (
    DEFAULT_DATABASE_HOST,
    DEFAULT_DATABASE_NAME,
    DEFAULT_DATABASE_PORT,
    DEFAULT_DATABASE_USER,
    Settings,
)


MODELS_APP = {
    "models": [
        "app.database.models",
        "aerich.models",
    ],
    "default_connection": "default",
}


def build_mysql_dsn(settings: Settings) -> str:
    """沿用现有配置语义，派生 Tortoise MySQL DSN。"""
    password = quote_plus(settings.database.password)
    user = quote_plus(settings.database.user)
    database = quote_plus(settings.database.database)
    return (
        f"mysql://{user}:{password}@{settings.database.host}:{settings.database.port}/{database}"
        f"?charset=utf8mb4"
    )


def build_tortoise_config(settings: Settings, *, connection_url: str | None = None) -> dict[str, object]:
    """构造 Tortoise/Aerich 共用配置。"""
    return {
        "connections": {
            "default": connection_url or build_mysql_dsn(settings),
        },
        "apps": {
            "models": MODELS_APP,
        },
        "use_tz": False,
        "timezone": "UTC",
    }


def build_sqlite_test_config(database_url: str = "sqlite://:memory:") -> dict[str, object]:
    """为测试提供轻量 SQLite 配置。"""
    return {
        "connections": {
            "default": database_url,
        },
        "apps": {
            "models": MODELS_APP,
        },
        "use_tz": False,
        "timezone": "UTC",
    }


async def init_database(settings: Settings, *, tortoise_config: dict[str, object] | None = None) -> None:
    """初始化 ORM 连接，不在应用启动时执行 migration。"""
    await Tortoise.init(config=tortoise_config or build_tortoise_config(settings))


async def close_database() -> None:
    """关闭 ORM 连接。"""
    await Tortoise.close_connections()


@asynccontextmanager
async def transaction_context():
    """统一事务上下文，供仓储层复用。"""
    async with tortoise_in_transaction() as connection:
        yield connection


def _load_db_config_for_aerich() -> tuple[str, int, str, str, str]:
    """为 Aerich 导入阶段惰性解析数据库配置，避免依赖完整应用配置。"""
    config_path = Path(os.environ.get("APP_CONFIG_PATH") or Path(__file__).resolve().parents[2] / "config.yaml")
    config_data: dict[str, object] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            config_data = loaded
    database_section = config_data.get("database") if isinstance(config_data.get("database"), dict) else {}
    host = (os.environ.get("DATABASE_HOST") or database_section.get("host") or DEFAULT_DATABASE_HOST).strip()
    port = int(os.environ.get("DATABASE_PORT") or database_section.get("port") or DEFAULT_DATABASE_PORT)
    database = (os.environ.get("DATABASE_NAME") or database_section.get("name") or DEFAULT_DATABASE_NAME).strip()
    user = (os.environ.get("DATABASE_USER") or database_section.get("user") or DEFAULT_DATABASE_USER).strip()
    password = str(os.environ.get("DATABASE_PASSWORD") or database_section.get("password") or "")
    return host, port, database, user, password


def _build_aerich_default_dsn() -> str:
    host, port, database, user, password = _load_db_config_for_aerich()
    return f"mysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}?charset=utf8mb4"


def get_tortoise_orm(settings: Settings) -> dict[str, object]:
    """为调用方显式提供 ORM 配置。"""
    return build_tortoise_config(settings)


TORTOISE_ORM = {
    "connections": {
        "default": _build_aerich_default_dsn(),
    },
    "apps": {
        "models": MODELS_APP,
    },
    "use_tz": False,
    "timezone": "UTC",
}


__all__ = [
    "TORTOISE_ORM",
    "build_mysql_dsn",
    "build_sqlite_test_config",
    "build_tortoise_config",
    "close_database",
    "get_tortoise_orm",
    "init_database",
    "transaction_context",
]
