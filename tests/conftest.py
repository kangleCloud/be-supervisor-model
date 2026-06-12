"""测试公共夹具。"""
from __future__ import annotations

import asyncio
import importlib
import re
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tortoise import Tortoise

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.database.bootstrap import build_tortoise_config
from app.database.models.auth import UserModel


PROGRAM_PATTERN = re.compile(r"\[program:(?P<name>[^\]]+)\]")
PORT_PATTERN = re.compile(r"(?:-Dserver\.port=|server\.port=|port=)(?P<port>\d+)")


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
        self.extra_listeners: dict[int, str] = {}

    def _current_programs(self) -> list[str]:
        programs: list[str] = []
        for path in sorted(self.conf_dir.rglob("*.ini")):
            content = path.read_text(encoding="utf-8")
            match = PROGRAM_PATTERN.search(content)
            if match:
                programs.append(match.group("name"))
        return programs

    def _current_listeners(self) -> list[tuple[int, str]]:
        listeners: list[tuple[int, str]] = []
        for path in sorted(self.conf_dir.rglob("*.ini")):
            content = path.read_text(encoding="utf-8")
            program_match = PROGRAM_PATTERN.search(content)
            port_match = PORT_PATTERN.search(content)
            if program_match is None or port_match is None:
                continue
            program_name = program_match.group("name")
            if self.states.get(program_name, "STOPPED") != "RUNNING":
                continue
            listeners.append((int(port_match.group("port")), program_name))
        listeners.extend((port, label) for port, label in sorted(self.extra_listeners.items()))
        return listeners

    def run(self, command, capture_output, text, timeout, check=False):  # noqa: ANN001, ARG002
        args = list(command)
        if args[:2] == ["ss", "-lnutp"]:
            lines = ["Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process"]
            for port, label in self._current_listeners():
                lines.append(f'tcp LISTEN 0 4096 0.0.0.0:{port} 0.0.0.0:* users:(("{label}",pid=1,fd=3))')
            return SimpleNamespace(returncode=0, stdout="\n".join(lines), stderr="")
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
                    state = self.states.get(name, "RUNNING")
                    detail = "pid 1, uptime 0:00:10" if state == "RUNNING" else "Not started"
                    lines.append(f"{name} {state} {detail}")
                return SimpleNamespace(returncode=0, stdout="\n".join(lines), stderr="")

            name = args[2]
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            state = self.states.get(name, "RUNNING")
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


class SQLiteTestDB:
    """用真实 SQLite 表替代 FakeMySQL 的主要测试能力。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.fail_next_supervisor_insert = False
        self.fail_next_supervisor_update = False
        self.fail_next_supervisor_delete = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @property
    def tables(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        with self._connect() as connection:
            cursor = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            table_names = [row[0] for row in cursor.fetchall()]
            for table_name in table_names:
                if table_name.startswith("sqlite_"):
                    continue
                rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table_name} ORDER BY id ASC")]
                if table_name == "sys_supervisor_service":
                    for row in rows:
                        row.setdefault("program_name", row.get("content_program_name"))
                        row.setdefault("config_name", row.get("file_name"))
                result[table_name] = rows
        return result

    def seed_user(
        self,
        user_name: str,
        password: str,
        *,
        user_id: int | None = None,
        nick_name: str | None = None,
        status: int = 1,
        is_super_admin: int = 0,
    ) -> None:
        with self._connect() as connection:
            if user_id is None:
                current = connection.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM sys_user").fetchone()[0]
                user_id = int(current)
            connection.execute(
                """
                INSERT INTO sys_user(
                    id, tenant_id, user_name, nick_name, password, status, is_super_admin,
                    login_time, login_address, pwd_update_date, is_deleted,
                    create_by_id, create_by, update_by_id, update_by, version, remark
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    0,
                    user_name,
                    nick_name,
                    hash_password(password),
                    status,
                    is_super_admin,
                    None,
                    None,
                    None,
                    0,
                    0,
                    "system",
                    0,
                    "system",
                    0,
                    "测试账号",
                ),
            )
            connection.commit()

    def seed_supervisor_service(
        self,
        *,
        host_ip: str,
        job_name: str | None,
        module_name: str | None,
        program_name: str,
        config_name: str,
        config_path: str | None = None,
        file_name: str | None = None,
        content_program_name: str | None = None,
        manage_mode: str = "TEMPLATE_MANAGED",
        baseline_content: str = "",
        metadata_complete: bool = True,
        parse_warnings: str = "[]",
        java_path: str | None = None,
        active_profile: str | None = None,
        port: int | None = None,
        jar_name: str | None = None,
        xms: str | None = None,
        xmx: str | None = None,
        run_user: str | None = None,
        status: str = "UNKNOWN",
        pid: str | None = None,
        uptime: str | None = None,
        status_sync_time: str | None = None,
        command: str | None = None,
        directory: str | None = None,
        stdout_logfile: str | None = None,
        has_backup: bool = False,
        config_content: str | None = None,
        backup_config_content: str | None = None,
        last_sync_at: str | None = None,
        sync_status: str = "UNKNOWN",
        sync_error: str | None = None,
        is_archived: bool = False,
        archived_at: str | None = None,
        restored_at: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sys_supervisor_service(
                    host_ip, job_name, module_name, content_program_name,
                    config_path, file_name, manage_mode,
                    baseline_content, metadata_complete, parse_warnings,
                    java_path, active_profile, port, jar_name, xms, xmx, run_user,
                    status, pid, uptime, status_sync_time,
                    command, directory, stdout_logfile, has_backup,
                    config_content, backup_config_content, last_sync_at,
                    sync_status, sync_error,
                    is_archived, archived_at, restored_at,
                    create_by_id, create_by, update_by_id, update_by, remark
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    host_ip,
                    job_name,
                    module_name,
                    content_program_name or program_name,
                    config_path or config_name,
                    file_name or config_name,
                    manage_mode,
                    baseline_content,
                    int(metadata_complete),
                    parse_warnings,
                    java_path,
                    active_profile,
                    port,
                    jar_name,
                    xms,
                    xmx,
                    run_user,
                    status,
                    pid,
                    uptime,
                    status_sync_time,
                    command,
                    directory,
                    stdout_logfile,
                    int(has_backup),
                    config_content,
                    backup_config_content,
                    last_sync_at,
                    sync_status,
                    sync_error,
                    int(is_archived),
                    archived_at,
                    restored_at,
                    0,
                    "system",
                    0,
                    "system",
                    "测试服务",
                ),
            )
            connection.commit()


async def _seed_default_admin() -> None:
    existing = await UserModel.filter(user_name="admin", is_deleted=0).first()
    if existing is not None:
        return
    await UserModel.create(
        id=1,
        tenant_id=0,
        user_name="admin",
        nick_name="超级管理员",
        password=hash_password("Admin@123456"),
        status=1,
        is_super_admin=1,
        create_by_id=0,
        create_by="system",
        update_by_id=0,
        update_by="system",
        version=0,
        remark="系统初始化超级管理员，请尽快重置默认密码",
    )


async def _init_sqlite_database(settings, db_path: Path) -> None:
    if getattr(Tortoise, "_inited", False):
        await Tortoise.close_connections()
    await Tortoise.init(config=build_tortoise_config(settings, connection_url=f"sqlite://{db_path}"))
    await Tortoise.generate_schemas(safe=True)
    await _seed_default_admin()


async def _close_sqlite_database() -> None:
    if getattr(Tortoise, "_inited", False):
        await Tortoise.close_connections()


@pytest.fixture()
def test_environment(tmp_path, monkeypatch):
    """准备测试配置。"""
    conf_dir = tmp_path / "supervisord.d"
    conf_dir.mkdir()
    inventory_path = tmp_path / "inventory.ini"
    inventory_path.write_text("[web]\n10.1.0.104\n", encoding="utf-8")
    db_path = tmp_path / "test.sqlite3"
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
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("APP_CONFIG_PATH", str(config_path))
    get_settings.cache_clear()
    yield {"conf_dir": conf_dir, "config_path": config_path, "db_path": db_path, "build_ini": _build_ini}
    get_settings.cache_clear()


@pytest.fixture()
def fake_mysql(test_environment, monkeypatch):
    """保留 fixture 名称，但底层切到 SQLite 真实表。"""
    helper = SQLiteTestDB(test_environment["db_path"])

    from app.database import bootstrap as bootstrap_module
    from app.database.repositories.supervisor import SupervisorServiceRepository

    original_create = SupervisorServiceRepository.create
    original_update_by_id = SupervisorServiceRepository.update_by_id
    original_delete_by_id = SupervisorServiceRepository.delete_by_id

    async def create_with_fail(self, payload, *, using_db=None):
        if helper.fail_next_supervisor_insert:
            helper.fail_next_supervisor_insert = False
            raise RuntimeError("模拟 Supervisor 主数据写库失败")
        return await original_create(self, payload, using_db=using_db)

    async def update_with_fail(self, record_id, payload, *, using_db=None):
        if helper.fail_next_supervisor_update:
            helper.fail_next_supervisor_update = False
            raise RuntimeError("模拟 Supervisor 主数据更新失败")
        return await original_update_by_id(self, record_id, payload, using_db=using_db)

    async def delete_with_fail(self, record_id, *, using_db=None):
        if helper.fail_next_supervisor_delete:
            helper.fail_next_supervisor_delete = False
            raise RuntimeError("模拟 Supervisor 主数据删除失败")
        return await original_delete_by_id(self, record_id, using_db=using_db)

    async def test_init_database(settings, *, tortoise_config=None):
        del tortoise_config
        await _init_sqlite_database(settings, test_environment["db_path"])

    async def test_close_database():
        await _close_sqlite_database()

    monkeypatch.setattr(bootstrap_module, "init_database", test_init_database)
    monkeypatch.setattr(bootstrap_module, "close_database", test_close_database)
    script_module = sys.modules.get("scripts.import_supervisor_services")
    if script_module is not None:
        monkeypatch.setattr(script_module, "init_database", test_init_database)
        monkeypatch.setattr(script_module, "close_database", test_close_database)
    monkeypatch.setattr(SupervisorServiceRepository, "create", create_with_fail)
    monkeypatch.setattr(SupervisorServiceRepository, "update_by_id", update_with_fail)
    monkeypatch.setattr(SupervisorServiceRepository, "delete_by_id", delete_with_fail)
    return helper


@pytest.fixture()
def fake_supervisor(test_environment, monkeypatch):
    """模拟本地 supervisorctl。"""
    controller = FakeSupervisorCtl(test_environment["conf_dir"])
    local_module = importlib.import_module("app.executor.local")
    monkeypatch.setattr(local_module.subprocess, "run", controller.run)
    return controller


@pytest.fixture()
def settings(test_environment):
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
        user_id: int | None = None,
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
def run_db(settings, test_environment):
    """在同一事件循环中初始化 Tortoise 并执行异步测试场景。"""

    def _run(coro_factory: Callable[[], Any]):
        async def _runner():
            await _init_sqlite_database(settings, test_environment["db_path"])
            try:
                return await coro_factory()
            finally:
                await _close_sqlite_database()

        return asyncio.run(_runner())

    return _run


@pytest.fixture()
def client(test_environment, fake_mysql, fake_supervisor, monkeypatch):
    """创建测试客户端，并把应用生命周期切到 SQLite。"""
    import app.api.supervisor as api_module
    import app.main as app_main_module
    from app.services.host_service import HostService
    from app.services.template_service import TemplateService
    from app.services.supervisor_registry_service import ImportStagingService, SupervisorRegistryService
    from app.services.config_file_service import ConfigFileService
    from app.services.port_check_service import PortCheckService
    from app.services.supervisor_service import SupervisorService
    from app.services.supervisor_detail_service import SupervisorDetailService
    from app.services.supervisor_import_service import SupervisorImportService
    from app.services.supervisor_mutation_service import SupervisorMutationService
    from app.services.supervisor_runtime_service import SupervisorRuntimeService
    from app.services.supervisor_archive_service import SupervisorArchiveService
    from app.services.supervisor_sync_service import SupervisorSyncService
    from app.services.supervisor_overview_service import SupervisorOverviewService

    async def test_init_database(settings):
        await _init_sqlite_database(settings, test_environment["db_path"])

    async def test_close_database():
        await _close_sqlite_database()

    monkeypatch.setattr(app_main_module, "init_database", test_init_database)
    monkeypatch.setattr(app_main_module, "close_database", test_close_database)

    get_settings.cache_clear()
    settings = get_settings()

    api_module._settings = settings
    api_module._host_service = HostService(settings)
    api_module._template_service = TemplateService(settings)
    api_module._registry_service = SupervisorRegistryService(settings)
    api_module._staging_service = ImportStagingService(settings)
    api_module._config_file_service = ConfigFileService(settings, api_module._host_service, api_module._template_service)
    api_module._port_check_service = PortCheckService(api_module._config_file_service, api_module._host_service)
    api_module._supervisor_service = SupervisorService(api_module._host_service)
    api_module._detail_service = SupervisorDetailService(api_module._host_service, api_module._registry_service)
    api_module._import_service = SupervisorImportService(
        api_module._host_service,
        api_module._config_file_service,
        api_module._template_service,
        api_module._registry_service,
        api_module._staging_service,
    )
    api_module._mutation_service = SupervisorMutationService(
        api_module._host_service, api_module._template_service, api_module._config_file_service,
        api_module._port_check_service, api_module._supervisor_service, api_module._registry_service,
    )
    api_module._runtime_service = SupervisorRuntimeService(api_module._host_service, api_module._registry_service, api_module._supervisor_service)
    api_module._archive_service = SupervisorArchiveService(api_module._host_service, api_module._config_file_service, api_module._registry_service, api_module._supervisor_service)
    api_module._sync_service = SupervisorSyncService(
        api_module._host_service, api_module._config_file_service, api_module._registry_service, api_module._supervisor_service, api_module._template_service,
    )
    api_module._overview_service = SupervisorOverviewService(settings, api_module._host_service)

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
