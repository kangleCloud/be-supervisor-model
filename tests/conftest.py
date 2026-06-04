"""测试公共夹具。"""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.passwords import hash_password


PROGRAM_PATTERN = re.compile(r"\[program:(?P<name>[^\]]+)\]")


def _build_ini(program_name: str, port: int, job_name: str = "demo", module_name: str = "member") -> str:
    return (
        f"[program:{program_name}]\n"
        f"command=/usr/local/jdk17/bin/java -jar -Xms128m -Xmx128m "
        f"-Dspring.profiles.active=prod -Dserver.port={port} "
        f"/data/content/{job_name}/{module_name}/{module_name}.jar\n"
        f"directory=/data/content/{job_name}/{module_name}\n"
        "autostart=true\n"
        "startsecs=10\n"
        "autorestart=true\n"
        "startretries=3\n"
        "user=root\n"
        "priority=999\n"
        "redirect_stderr=true\n"
        "stdout_logfile_maxbytes=1GB\n"
        "stdout_logfile_backups=1\n"
        "stopasgroup=false\n"
        "killasgroup=false\n"
        f"stdout_logfile=/data/logs/{job_name}/{job_name}-{module_name}.log\n"
    )


class FakeSupervisorCtl:
    """模拟 supervisorctl 行为。"""

    def __init__(self, conf_dir: Path):
        self.conf_dir = conf_dir
        self.states: dict[str, str] = {}

    def _current_programs(self) -> list[str]:
        programs: list[str] = []
        for path in sorted(self.conf_dir.glob("*.ini")):
            content = path.read_text(encoding="utf-8")
            match = PROGRAM_PATTERN.search(content)
            if match:
                programs.append(match.group("name"))
        return programs

    def run(self, command, capture_output, text, timeout, check=False):  # noqa: ANN001
        args = list(command)
        if not args or args[0] != "supervisorctl":
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        action = args[1]
        current_programs = self._current_programs()
        for name in current_programs:
            self.states.setdefault(name, "STOPPED")

        if action == "status":
            if len(args) == 2:
                lines = []
                for name in current_programs:
                    state = self.states.get(name, "STOPPED")
                    detail = "pid 1, uptime 0:00:10" if state == "RUNNING" else "Not started"
                    lines.append(f"{name} {state} {detail}")
                return SimpleNamespace(returncode=0, stdout="\n".join(lines), stderr="")

            name = args[2]
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            state = self.states.get(name, "STOPPED")
            detail = "pid 1, uptime 0:00:10" if state == "RUNNING" else "Not started"
            return SimpleNamespace(returncode=0, stdout=f"{name} {state} {detail}", stderr="")

        if len(args) < 3 and action in {"start", "stop", "restart"}:
            return SimpleNamespace(returncode=2, stdout="", stderr="missing program name")

        name = args[2] if len(args) >= 3 else ""
        if action == "start":
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            self.states[name] = "RUNNING"
            return SimpleNamespace(returncode=0, stdout=f"{name}: started", stderr="")
        if action == "stop":
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            if self.states.get(name) != "RUNNING":
                return SimpleNamespace(returncode=7, stdout="", stderr=f"{name}: ERROR (not running)")
            self.states[name] = "STOPPED"
            return SimpleNamespace(returncode=0, stdout=f"{name}: stopped", stderr="")
        if action == "restart":
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            self.states[name] = "RUNNING"
            return SimpleNamespace(returncode=0, stdout=f"{name}: restarted", stderr="")
        if action == "reread":
            return SimpleNamespace(returncode=0, stdout="No config updates to processes", stderr="")
        if action == "update":
            return SimpleNamespace(returncode=0, stdout="updated", stderr="")

        return SimpleNamespace(returncode=0, stdout="", stderr="")


class FakeMySQLCursor:
    """模拟最小 MySQL Cursor 能力，覆盖当前仓库用到的 SQL。"""

    def __init__(self, server: "FakeMySQLServer"):
        self.server = server
        self.results: list[dict[str, Any]] = []
        self.lastrowid = 0

    def __enter__(self) -> "FakeMySQLCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> int:
        return self.server.execute(query, params or (), self)

    def fetchone(self) -> dict[str, Any] | None:
        if not self.results:
            return None
        return dict(self.results[0])

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.results]


class FakeMySQLConnection:
    """模拟数据库连接对象。"""

    def __init__(self, server: "FakeMySQLServer", include_database: bool):
        self.server = server
        self.include_database = include_database

    def __enter__(self) -> "FakeMySQLConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def cursor(self) -> FakeMySQLCursor:
        return FakeMySQLCursor(self.server)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeMySQLServer:
    """使用内存字典模拟 MySQL 表数据，便于测试 JWT 与令牌逻辑。"""

    def __init__(self):
        self.databases: set[str] = set()
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.auto_increment: dict[str, int] = {
            "sys_login_log": 1,
            "sys_login_token": 1,
        }

    def connect_server(self):
        return FakeMySQLConnection(self, include_database=False)

    def connect_database(self):
        return FakeMySQLConnection(self, include_database=True)

    def seed_user(
        self,
        user_name: str,
        password: str,
        *,
        user_id: int = 1,
        nick_name: str | None = None,
        status: int = 1,
        is_super_admin: int = 0,
    ) -> None:
        self.tables.setdefault("sys_user", [])
        self.tables["sys_user"].append(
            {
                "id": user_id,
                "tenant_id": 0,
                "user_name": user_name,
                "nick_name": nick_name,
                "password": hash_password(password),
                "status": status,
                "is_super_admin": is_super_admin,
                "login_time": None,
                "login_address": None,
                "pwd_update_date": None,
                "create_time": "2026-06-03 00:00:00",
                "update_time": "2026-06-03 00:00:00",
                "is_deleted": 0,
                "create_by_id": 0,
                "create_by": "system",
                "update_by_id": 0,
                "update_by": "system",
                "version": 0,
                "remark": "测试账号",
            }
        )

    def execute(self, query: str, params: tuple[Any, ...], cursor: FakeMySQLCursor) -> int:
        normalized = " ".join(query.strip().split())
        cursor.results = []
        cursor.lastrowid = 0

        if normalized.startswith("CREATE DATABASE IF NOT EXISTS"):
            database_name = normalized.split("`")[1]
            self.databases.add(database_name)
            return 1

        if normalized.startswith("CREATE TABLE IF NOT EXISTS"):
            table_name = normalized.split("`")[1] if "`" in normalized else normalized.split()[5]
            self.tables.setdefault(table_name, [])
            return 1

        if normalized == "SHOW TABLES LIKE %s":
            table_name = str(params[0])
            cursor.results = [{"table_name": table_name}] if table_name in self.tables else []
            return len(cursor.results)

        if normalized == "SELECT version FROM sys_schema_migration":
            cursor.results = [{"version": row["version"]} for row in self.tables.setdefault("sys_schema_migration", [])]
            return len(cursor.results)

        if normalized.startswith("INSERT INTO sys_schema_migration"):
            version, name = params
            self.tables.setdefault("sys_schema_migration", []).append({"version": int(version), "name": str(name)})
            return 1

        if "FROM sys_user WHERE user_name = %s AND is_deleted = 0 LIMIT 1" in normalized:
            username = str(params[0])
            row = next(
                (item for item in self.tables.get("sys_user", []) if item["user_name"] == username and item["is_deleted"] == 0),
                None,
            )
            cursor.results = [row] if row else []
            return len(cursor.results)

        if "FROM sys_user WHERE id = %s AND is_deleted = 0 LIMIT 1" in normalized:
            user_id = int(params[0])
            row = next(
                (item for item in self.tables.get("sys_user", []) if int(item["id"]) == user_id and item["is_deleted"] == 0),
                None,
            )
            cursor.results = [row] if row else []
            return len(cursor.results)

        if normalized.startswith("UPDATE sys_user SET login_time = %s"):
            login_time, login_address, update_by_id, update_by, user_id = params
            for item in self.tables.get("sys_user", []):
                if int(item["id"]) == int(user_id) and item["is_deleted"] == 0:
                    item["login_time"] = login_time
                    item["login_address"] = login_address
                    item["update_by_id"] = update_by_id
                    item["update_by"] = update_by
                    item["version"] = int(item["version"]) + 1
                    return 1
            return 0

        if normalized.startswith("INSERT INTO sys_login_token("):
            token_id = self.auto_increment["sys_login_token"]
            self.auto_increment["sys_login_token"] += 1
            row = {
                "id": token_id,
                "tenant_id": None,
                "user_id": int(params[0]),
                "user_name": str(params[1]),
                "token_jti": str(params[2]),
                "token_digest": str(params[3]),
                "login_ip": str(params[4]),
                "user_agent": str(params[5]),
                "issued_at": params[6],
                "expires_at": params[7],
                "revoked_time": None,
                "create_by_id": params[8],
                "create_by": params[9],
                "update_by_id": params[10],
                "update_by": params[11],
                "remark": params[12],
                "is_deleted": 0,
                "version": 0,
            }
            self.tables.setdefault("sys_login_token", []).append(row)
            cursor.lastrowid = token_id
            return 1

        if "FROM sys_login_token WHERE user_id = %s AND token_jti = %s AND is_deleted = 0 AND revoked_time IS NULL LIMIT 1" in normalized:
            user_id, token_jti = int(params[0]), str(params[1])
            row = next(
                (
                    item
                    for item in self.tables.get("sys_login_token", [])
                    if int(item["user_id"]) == user_id
                    and item["token_jti"] == token_jti
                    and item["is_deleted"] == 0
                    and item["revoked_time"] is None
                ),
                None,
            )
            cursor.results = [row] if row else []
            return len(cursor.results)

        if normalized.startswith("UPDATE sys_login_token SET revoked_time = %s"):
            revoked_time, update_by_id, update_by, token_id = params
            for item in self.tables.get("sys_login_token", []):
                if int(item["id"]) == int(token_id) and item["revoked_time"] is None:
                    item["revoked_time"] = revoked_time
                    item["update_by_id"] = update_by_id
                    item["update_by"] = update_by
                    item["version"] = int(item["version"]) + 1
                    return 1
            return 0

        if normalized.startswith("INSERT INTO sys_login_log("):
            log_id = self.auto_increment["sys_login_log"]
            self.auto_increment["sys_login_log"] += 1
            self.tables.setdefault("sys_login_log", []).append(
                {
                    "id": log_id,
                    "user_id": params[0],
                    "token_id": params[1],
                    "user_name": params[2],
                    "ipaddr": params[3],
                    "login_location": params[4],
                    "browser": params[5],
                    "os": params[6],
                    "status": params[7],
                    "msg": params[8],
                    "token_jti": params[9],
                    "login_time": params[10],
                    "create_by_id": params[11],
                    "create_by": params[12],
                    "update_by_id": params[13],
                    "update_by": params[14],
                    "remark": params[15],
                }
            )
            cursor.lastrowid = log_id
            return 1

        raise AssertionError(f"Unsupported SQL: {normalized}")


@pytest.fixture()
def test_environment(tmp_path, monkeypatch):
    """准备测试配置。"""
    conf_dir = tmp_path / "supervisord.d"
    conf_dir.mkdir()
    inventory_path = tmp_path / "inventory.ini"
    inventory_path.write_text("[web]\n10.1.0.104\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "app:",
                "  host: 127.0.0.1",
                "  port: 18880",
                "  logLevel: info",
                "database:",
                "  host: 127.0.0.1",
                "  port: 3306",
                "  name: supervisor_model_test",
                "  user: root",
                "  password: ''",
                "  connectTimeoutSeconds: 5",
                "auth:",
                "  jwtSecret: test-jwt-secret-0123456789abcdef",
                "  accessTokenExpireMinutes: 480",
                "supervisor:",
                f"  confDir: {conf_dir}",
                "  commandTimeoutSeconds: 30",
                "executor:",
                "  type: local",
                f"  inventoryPath: {inventory_path}",
                "  remoteUser: root",
                "  timeoutSeconds: 30",
                "hosts:",
                "  - name: local",
                "    ip: 127.0.0.1",
                "    enabled: true",
                "    executorType: local",
                "  - name: web-104",
                "    ip: 10.1.0.104",
                "    enabled: true",
                "    executorType: ansible",
                "    ansiblePattern: web-104",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("APP_CONFIG_PATH", str(config_path))
    get_settings.cache_clear()
    yield {"conf_dir": conf_dir, "config_path": config_path, "build_ini": _build_ini}
    get_settings.cache_clear()


@pytest.fixture()
def fake_mysql(monkeypatch):
    """模拟 MySQL 服务端，避免单测依赖真实数据库。"""
    server = FakeMySQLServer()
    database_module = importlib.import_module("app.core.database")
    monkeypatch.setattr(database_module, "connect_server", lambda settings: server.connect_server())
    monkeypatch.setattr(database_module, "connect_database", lambda settings: server.connect_database())
    return server


@pytest.fixture()
def fake_supervisor(test_environment, monkeypatch):
    """模拟本地 supervisorctl。"""
    controller = FakeSupervisorCtl(test_environment["conf_dir"])
    local_module = importlib.import_module("app.executor.local")
    monkeypatch.setattr(local_module.subprocess, "run", controller.run)
    return controller


@pytest.fixture()
def settings(test_environment, fake_mysql):
    """返回测试配置对象。"""
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def seed_user(fake_mysql):
    """提供测试用户建档辅助函数。"""

    def _seed_user(
        user_name: str = "ops",
        password: str = "secret",
        *,
        user_id: int = 1,
        nick_name: str | None = "运维用户",
        status: int = 1,
        is_super_admin: int = 0,
    ) -> None:
        fake_mysql.seed_user(
            user_name=user_name,
            password=password,
            user_id=user_id,
            nick_name=nick_name,
            status=status,
            is_super_admin=is_super_admin,
        )

    return _seed_user


@pytest.fixture()
def client(test_environment, fake_mysql, fake_supervisor):
    """创建测试客户端。"""
    get_settings.cache_clear()
    main_module = importlib.import_module("app.main")
    app = main_module.create_app()
    return TestClient(app)
