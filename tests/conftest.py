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

    def run(self, command, capture_output, text, timeout, check=False):  # noqa: ANN001
        args = list(command)
        if args[:2] == ["ss", "-lnutp"]:
            lines = ["Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process"]
            for port, label in self._current_listeners():
                lines.append(f"tcp LISTEN 0 4096 0.0.0.0:{port} 0.0.0.0:* users:((\"{label}\",pid=1,fd=3))")
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


class FakeMySQLCursor:
    """模拟最小 MySQL Cursor 能力，覆盖当前仓库用到的 SQL。"""

    def __init__(self, server: "FakeMySQLServer"):
        self.server = server
        self.results: list[dict[str, Any]] = []
        self.lastrowid = 0
        self.rowcount = 0

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
        self.table_columns: dict[str, set[str]] = {}
        self.table_indexes: dict[str, set[str]] = {}
        self.auto_increment: dict[str, int] = {
            "sys_login_log": 1,
            "sys_login_token": 1,
            "sys_supervisor_service": 1,
            "sys_supervisor_import_staging": 1,
        }
        self.fail_next_supervisor_insert = False
        self.fail_next_supervisor_update = False
        self.fail_next_supervisor_delete = False

    def connect_server(self):
        return FakeMySQLConnection(self, include_database=False)

    def connect_database(self):
        return FakeMySQLConnection(self, include_database=True)

    def set_table_schema(
        self,
        table_name: str,
        *,
        columns: set[str] | None = None,
        indexes: set[str] | None = None,
    ) -> None:
        """测试可显式指定表结构，模拟旧库缺列场景。"""
        if columns is not None:
            self.table_columns[table_name] = set(columns)
        if indexes is not None:
            self.table_indexes[table_name] = set(indexes)

    @staticmethod
    def _default_columns_for_table(table_name: str) -> set[str]:
        if table_name == "sys_supervisor_service":
            return {
                "id",
                "host_ip",
                "config_path",
                "file_name",
                "content_program_name",
                "manage_mode",
                "baseline_content",
                "metadata_complete",
                "parse_warnings",
                "job_name",
                "module_name",
                "java_path",
                "active_profile",
                "port",
                "jar_name",
                "xms",
                "xmx",
                "run_user",
                "status",
                "pid",
                "uptime",
                "status_sync_time",
                "command",
                "directory",
                "stdout_logfile",
                "has_backup",
                "config_content",
                "backup_config_content",
                "last_sync_at",
                "sync_status",
                "sync_error",
                "is_archived",
                "archived_at",
                "restored_at",
                "create_time",
                "update_time",
                "create_by_id",
                "create_by",
                "update_by_id",
                "update_by",
                "remark",
            }
        if table_name == "sys_supervisor_import_staging":
            return {
                "id",
                "batch_id",
                "host_ip",
                "operator_id",
                "operator_name",
                "config_path",
                "file_name",
                "content_program_name",
                "baseline_content",
                "metadata_complete",
                "parse_warnings",
                "job_name",
                "module_name",
                "java_path",
                "active_profile",
                "port",
                "jar_name",
                "xms",
                "xmx",
                "run_user",
                "result",
                "message",
                "create_time",
            }
        return set()

    @staticmethod
    def _default_indexes_for_table(table_name: str) -> set[str]:
        if table_name == "sys_supervisor_service":
            return {
                "uk_supervisor_host_config_path",
                "idx_supervisor_host_program",
                "idx_supervisor_host_manage_mode",
                "idx_supervisor_host_archived",
                "idx_supervisor_host_status",
            }
        if table_name == "sys_supervisor_import_staging":
            return {
                "idx_staging_batch_id",
                "idx_staging_host_ip",
            }
        return set()

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
        if user_id is None:
            user_id = max((int(item["id"]) for item in self.tables.get("sys_user", [])), default=0) + 1
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
        self.tables.setdefault("sys_supervisor_service", [])
        record_id = self.auto_increment["sys_supervisor_service"]
        self.auto_increment["sys_supervisor_service"] += 1
        self.tables["sys_supervisor_service"].append(
            self._build_supervisor_row(
                record_id=record_id,
                host_ip=host_ip,
                job_name=job_name,
                module_name=module_name,
                program_name=program_name,
                config_name=config_name,
                config_path=config_path or config_name,
                file_name=file_name or config_name,
                content_program_name=content_program_name or program_name,
                manage_mode=manage_mode,
                baseline_content=baseline_content,
                metadata_complete=metadata_complete,
                parse_warnings=parse_warnings,
                java_path=java_path,
                active_profile=active_profile,
                port=port,
                jar_name=jar_name,
                xms=xms,
                xmx=xmx,
                run_user=run_user,
                create_by_id=0,
                create_by="system",
                update_by_id=0,
                update_by="system",
                remark="测试服务",
                status=status,
                pid=pid,
                uptime=uptime,
                status_sync_time=status_sync_time,
                command=command,
                directory=directory,
                stdout_logfile=stdout_logfile,
                has_backup=has_backup,
                config_content=config_content,
                backup_config_content=backup_config_content,
                last_sync_at=last_sync_at,
                sync_status=sync_status,
                sync_error=sync_error,
                is_archived=is_archived,
                archived_at=archived_at,
                restored_at=restored_at,
            )
        )

    def _build_supervisor_row(
        self,
        *,
        record_id: int,
        host_ip: str,
        job_name: str | None,
        module_name: str | None,
        program_name: str,
        config_name: str,
        config_path: str,
        file_name: str,
        content_program_name: str,
        manage_mode: str,
        baseline_content: str,
        metadata_complete: bool,
        parse_warnings: str,
        java_path: str | None,
        active_profile: str | None,
        port: int | None,
        jar_name: str | None,
        xms: str | None,
        xmx: str | None,
        run_user: str | None,
        create_by_id: int,
        create_by: str,
        update_by_id: int,
        update_by: str,
        remark: str,
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
    ) -> dict[str, Any]:
        return {
            "id": record_id,
            "host_ip": host_ip,
            "job_name": job_name,
            "module_name": module_name,
            "program_name": program_name,
            "config_name": config_name,
            "config_path": config_path,
            "file_name": file_name,
            "content_program_name": content_program_name,
            "manage_mode": manage_mode,
            "baseline_content": baseline_content,
            "metadata_complete": 1 if metadata_complete else 0,
            "parse_warnings": parse_warnings,
            "java_path": java_path,
            "active_profile": active_profile,
            "port": port,
            "jar_name": jar_name,
            "xms": xms,
            "xmx": xmx,
            "run_user": run_user,
            "status": status,
            "pid": pid,
            "uptime": uptime,
            "status_sync_time": status_sync_time,
            "command": command,
            "directory": directory,
            "stdout_logfile": stdout_logfile,
            "has_backup": 1 if has_backup else 0,
            "config_content": config_content,
            "backup_config_content": backup_config_content,
            "last_sync_at": last_sync_at,
            "sync_status": sync_status,
            "sync_error": sync_error,
            "is_archived": 1 if is_archived else 0,
            "archived_at": archived_at,
            "restored_at": restored_at,
            "create_by_id": create_by_id,
            "create_by": create_by,
            "update_by_id": update_by_id,
            "update_by": update_by,
            "remark": remark,
        }

    @staticmethod
    def _hydrate_supervisor_defaults(row: dict[str, Any]) -> dict[str, Any]:
        row.setdefault("config_path", row.get("config_name"))
        row.setdefault("file_name", row.get("config_name"))
        row.setdefault("content_program_name", row.get("program_name"))
        row.setdefault("manage_mode", "TEMPLATE_MANAGED")
        row.setdefault("baseline_content", "")
        row.setdefault("metadata_complete", 1)
        row.setdefault("parse_warnings", "[]")
        row.setdefault("status", "UNKNOWN")
        row.setdefault("pid", None)
        row.setdefault("uptime", None)
        row.setdefault("status_sync_time", None)
        row.setdefault("command", None)
        row.setdefault("directory", None)
        row.setdefault("stdout_logfile", None)
        row.setdefault("has_backup", 0)
        row.setdefault("config_content", None)
        row.setdefault("backup_config_content", None)
        row.setdefault("last_sync_at", None)
        row.setdefault("sync_status", "UNKNOWN")
        row.setdefault("sync_error", None)
        row.setdefault("is_archived", 0)
        row.setdefault("archived_at", None)
        row.setdefault("restored_at", None)
        row.setdefault("create_by_id", 0)
        row.setdefault("create_by", "system")
        row.setdefault("update_by_id", 0)
        row.setdefault("update_by", "system")
        row.setdefault("remark", "测试服务")
        return row

    @staticmethod
    def _filter_supervisor_rows(
        rows: list[dict[str, Any]],
        where_part: str,
        params: tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        prepared_rows = [dict(FakeMySQLServer._hydrate_supervisor_defaults(dict(row))) for row in rows]
        filtered_rows = prepared_rows
        param_idx = 0
        for clause in [item.strip() for item in where_part.split(" AND ") if item.strip()]:
            if clause == "host_ip = %s":
                host_val = str(params[param_idx])
                filtered_rows = [row for row in filtered_rows if str(row.get("host_ip", "")) == host_val]
                param_idx += 1
                continue
            if clause.startswith("(content_program_name LIKE %s"):
                keyword_val = str(params[param_idx]).replace("%", "").lower()
                filtered_rows = [
                    row for row in filtered_rows
                    if keyword_val in str(row.get("content_program_name", "")).lower()
                    or keyword_val in str(row.get("file_name", "")).lower()
                    or keyword_val in str(row.get("job_name", "") or "").lower()
                    or keyword_val in str(row.get("module_name", "") or "").lower()
                    or keyword_val in str(row.get("port", "") or "")
                ]
                param_idx += 5
                continue
            if clause == "status = %s":
                status_val = str(params[param_idx])
                filtered_rows = [row for row in filtered_rows if row.get("status", "UNKNOWN") == status_val]
                param_idx += 1
                continue
            if clause == "is_archived = %s":
                archived_val = int(params[param_idx])
                filtered_rows = [row for row in filtered_rows if int(row.get("is_archived", 0)) == archived_val]
                param_idx += 1
                continue
            if clause.startswith("is_archived = "):
                # Handle literal values like is_archived = 0 or is_archived = 1
                archived_val = int(clause.split("=")[1].strip())
                filtered_rows = [row for row in filtered_rows if int(row.get("is_archived", 0)) == archived_val]
                continue
        return filtered_rows

    @staticmethod
    def _matches_insert(normalized: str, table_name: str) -> bool:
        return normalized.startswith(f"INSERT INTO {table_name}(") or normalized.startswith(
            f"INSERT INTO `{table_name}`("
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
            self.table_columns.setdefault(table_name, self._default_columns_for_table(table_name))
            self.table_indexes.setdefault(table_name, self._default_indexes_for_table(table_name))
            return 1

        if normalized.startswith("ALTER TABLE"):
            table_name = normalized.split("`")[1] if "`" in normalized else normalized.split()[2]
            columns = self.table_columns.setdefault(table_name, set())
            indexes = self.table_indexes.setdefault(table_name, set())
            if "ADD COLUMN `status`" in normalized:
                columns.add("status")
            if "ADD COLUMN `pid`" in normalized:
                columns.add("pid")
            if "ADD COLUMN `uptime`" in normalized:
                columns.add("uptime")
            if "ADD COLUMN `status_sync_time`" in normalized:
                columns.add("status_sync_time")
            if "ADD COLUMN `command`" in normalized:
                columns.add("command")
            if "ADD COLUMN `directory`" in normalized:
                columns.add("directory")
            if "ADD COLUMN `stdout_logfile`" in normalized:
                columns.add("stdout_logfile")
            if "ADD COLUMN `has_backup`" in normalized:
                columns.add("has_backup")
            if "ADD COLUMN `config_content`" in normalized:
                columns.add("config_content")
            if "ADD COLUMN `backup_config_content`" in normalized:
                columns.add("backup_config_content")
            if "ADD COLUMN `last_sync_at`" in normalized:
                columns.add("last_sync_at")
            if "ADD COLUMN `sync_status`" in normalized:
                columns.add("sync_status")
            if "ADD COLUMN `sync_error`" in normalized:
                columns.add("sync_error")
            if "ADD COLUMN `is_archived`" in normalized:
                columns.add("is_archived")
            if "ADD COLUMN `archived_at`" in normalized:
                columns.add("archived_at")
            if "ADD COLUMN `restored_at`" in normalized:
                columns.add("restored_at")
            if "ADD KEY `idx_supervisor_host_status`" in normalized:
                indexes.add("idx_supervisor_host_status")
            if "ADD KEY `idx_supervisor_host_archived`" in normalized:
                indexes.add("idx_supervisor_host_archived")
            return 1

        if normalized == "SHOW TABLES LIKE %s":
            table_name = str(params[0])
            cursor.results = [{"table_name": table_name}] if table_name in self.tables else []
            return len(cursor.results)

        if normalized.startswith("SELECT 1 FROM information_schema.COLUMNS"):
            _, table_name, column_name = params
            columns = self.table_columns.get(str(table_name), set())
            cursor.results = [{"1": 1}] if str(column_name) in columns else []
            return len(cursor.results)

        if normalized.startswith("SHOW INDEX FROM `"):
            table_name = normalized.split("`")[1]
            cursor.results = [{"Key_name": index_name} for index_name in sorted(self.table_indexes.get(table_name, set()))]
            return len(cursor.results)

        if normalized == "SELECT version FROM sys_schema_migration":
            cursor.results = [{"version": row["version"]} for row in self.tables.setdefault("sys_schema_migration", [])]
            return len(cursor.results)

        if normalized.startswith("INSERT INTO sys_schema_migration"):
            version, name = params
            self.tables.setdefault("sys_schema_migration", []).append({"version": int(version), "name": str(name)})
            return 1

        if self._matches_insert(normalized, "sys_supervisor_import_staging"):
            staging_id = self.auto_increment["sys_supervisor_import_staging"]
            self.auto_increment["sys_supervisor_import_staging"] += 1
            self.tables.setdefault("sys_supervisor_import_staging", []).append(
                {
                    "id": staging_id,
                    "batch_id": str(params[0]),
                    "host_ip": str(params[1]),
                    "operator_id": int(params[2]),
                    "operator_name": str(params[3]),
                    "config_path": str(params[4]),
                    "file_name": str(params[5]),
                    "content_program_name": str(params[6]) if params[6] is not None else None,
                    "baseline_content": str(params[7]) if params[7] is not None else None,
                    "metadata_complete": int(params[8]),
                    "parse_warnings": str(params[9]) if params[9] is not None else None,
                    "job_name": str(params[10]) if params[10] is not None else None,
                    "module_name": str(params[11]) if params[11] is not None else None,
                    "java_path": str(params[12]) if params[12] is not None else None,
                    "active_profile": str(params[13]) if params[13] is not None else None,
                    "port": int(params[14]) if params[14] is not None else None,
                    "jar_name": str(params[15]) if params[15] is not None else None,
                    "xms": str(params[16]) if params[16] is not None else None,
                    "xmx": str(params[17]) if params[17] is not None else None,
                    "run_user": str(params[18]) if params[18] is not None else None,
                    "result": str(params[19]),
                    "message": str(params[20]) if params[20] is not None else None,
                    "create_time": "2026-06-11 00:00:00",
                }
            )
            cursor.lastrowid = staging_id
            return 1

        if normalized.startswith(
            "SELECT id, batch_id, host_ip, operator_id, operator_name, config_path, file_name, content_program_name,"
        ) and "FROM sys_supervisor_import_staging" in normalized:
            batch_id = str(params[0])
            host_ip = str(params[1])
            operator_id = int(params[2])
            rows = [
                dict(item)
                for item in self.tables.get("sys_supervisor_import_staging", [])
                if str(item.get("batch_id")) == batch_id
                and str(item.get("host_ip")) == host_ip
                and int(item.get("operator_id", 0)) == operator_id
            ]
            rows.sort(key=lambda row: int(row["id"]))
            cursor.results = rows
            return len(rows)

        if normalized == "DELETE FROM sys_supervisor_import_staging WHERE create_time < %s":
            expire_before = str(params[0])
            rows = self.tables.get("sys_supervisor_import_staging", [])
            before = len(rows)
            self.tables["sys_supervisor_import_staging"] = [
                item for item in rows if str(item.get("create_time", "")) >= expire_before
            ]
            deleted = before - len(self.tables["sys_supervisor_import_staging"])
            cursor.rowcount = deleted
            return deleted

        if normalized == "DELETE FROM sys_supervisor_import_staging WHERE host_ip = %s AND operator_id = %s":
            host_ip = str(params[0])
            operator_id = int(params[1])
            rows = self.tables.get("sys_supervisor_import_staging", [])
            before = len(rows)
            self.tables["sys_supervisor_import_staging"] = [
                item for item in rows
                if not (str(item.get("host_ip")) == host_ip and int(item.get("operator_id", 0)) == operator_id)
            ]
            deleted = before - len(self.tables["sys_supervisor_import_staging"])
            cursor.rowcount = deleted
            return deleted

        if normalized == "DELETE FROM sys_supervisor_import_staging WHERE batch_id = %s":
            batch_id = str(params[0])
            rows = self.tables.get("sys_supervisor_import_staging", [])
            before = len(rows)
            self.tables["sys_supervisor_import_staging"] = [
                item for item in rows if str(item.get("batch_id")) != batch_id
            ]
            deleted = before - len(self.tables["sys_supervisor_import_staging"])
            cursor.rowcount = deleted
            return deleted

        if normalized == "DELETE FROM sys_supervisor_import_staging WHERE batch_id = %s AND host_ip = %s AND operator_id = %s":
            batch_id = str(params[0])
            host_ip = str(params[1])
            operator_id = int(params[2])
            rows = self.tables.get("sys_supervisor_import_staging", [])
            before = len(rows)
            self.tables["sys_supervisor_import_staging"] = [
                item for item in rows
                if not (
                    str(item.get("batch_id")) == batch_id
                    and str(item.get("host_ip")) == host_ip
                    and int(item.get("operator_id", 0)) == operator_id
                )
            ]
            deleted = before - len(self.tables["sys_supervisor_import_staging"])
            cursor.rowcount = deleted
            return deleted

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

        if self._matches_insert(normalized, "sys_user"):
            active_admin = next(
                (item for item in self.tables.get("sys_user", []) if item["user_name"] == "admin" and item["is_deleted"] == 0),
                None,
            )
            if active_admin is not None:
                return 0
            next_user_id = max((int(item["id"]) for item in self.tables.get("sys_user", [])), default=0) + 1
            self.tables.setdefault("sys_user", []).append(
                {
                    "id": next_user_id,
                    "tenant_id": 0,
                    "user_name": "admin",
                    "nick_name": "超级管理员",
                    "password": "$2b$12$27nxsNqi/PQ8Yo3Py.cs/uWDVi.e1z7lQQhMbmm5AIEjhNRWodN7K",
                    "status": 1,
                    "is_super_admin": 1,
                    "login_time": None,
                    "login_address": None,
                    "pwd_update_date": "2026-06-04 00:00:00",
                    "create_time": "2026-06-04 00:00:00",
                    "update_time": "2026-06-04 00:00:00",
                    "is_deleted": 0,
                    "create_by_id": 0,
                    "create_by": "system",
                    "update_by_id": 0,
                    "update_by": "system",
                    "version": 0,
                    "remark": "系统初始化超级管理员，请尽快重置默认密码",
                }
            )
            return 1

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

        if self._matches_insert(normalized, "sys_login_token"):
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
            updated = 0
            for item in self.tables.get("sys_login_token", []):
                if int(item["id"]) == int(token_id) and item["revoked_time"] is None:
                    item["revoked_time"] = revoked_time
                    item["update_by_id"] = update_by_id
                    item["update_by"] = update_by
                    item["version"] = int(item["version"]) + 1
                    updated += 1
            cursor.rowcount = updated
            return updated

        if self._matches_insert(normalized, "sys_login_log"):
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

        if "FROM sys_supervisor_service" in normalized and "ORDER BY" in normalized:
            rows = list(self.tables.get("sys_supervisor_service", []))
            if "WHERE" in normalized:
                where_part = normalized.split("WHERE")[1].split("ORDER BY")[0].strip()
                rows = self._filter_supervisor_rows(rows, where_part, params)
            else:
                rows = [dict(self._hydrate_supervisor_defaults(dict(row))) for row in rows]
            rows.sort(key=lambda r: -int(r.get("id", 0)))
            has_limit = "LIMIT %s OFFSET %s" in normalized
            if has_limit and len(params) >= 2:
                limit_val = int(params[-2])
                offset_val = int(params[-1])
                rows = rows[offset_val:offset_val + limit_val]
            cursor.results = [dict(item) for item in rows]
            return len(cursor.results)

        # SELECT with content_program_name (new pattern)
        if "FROM sys_supervisor_service WHERE host_ip = %s AND content_program_name = %s LIMIT 1" in normalized:
            host_ip = str(params[0])
            content_program_name = str(params[1])
            row = next(
                (
                    dict(self._hydrate_supervisor_defaults(item))
                    for item in self.tables.get("sys_supervisor_service", [])
                    if item["host_ip"] == host_ip and item.get("content_program_name", item.get("program_name")) == content_program_name
                ),
                None,
            )
            cursor.results = [dict(row)] if row else []
            return len(cursor.results)

        if "FROM sys_supervisor_service WHERE host_ip = %s AND config_path = %s LIMIT 1" in normalized:
            host_ip = str(params[0])
            config_path = str(params[1])
            row = next(
                (
                    dict(self._hydrate_supervisor_defaults(item))
                    for item in self.tables.get("sys_supervisor_service", [])
                    if item["host_ip"] == host_ip and item["config_path"] == config_path
                ),
                None,
            )
            cursor.results = [dict(row)] if row else []
            return len(cursor.results)

        if self._matches_insert(normalized, "sys_supervisor_service"):
            if self.fail_next_supervisor_insert:
                self.fail_next_supervisor_insert = False
                raise RuntimeError("模拟 Supervisor 主数据写库失败")

            # New INSERT pattern: host_ip, job_name, module_name, content_program_name,
            # config_path, file_name, manage_mode, baseline_content, metadata_complete, parse_warnings,
            # java_path, active_profile, port, jar_name, xms, xmx, run_user,
            # create_by_id, create_by, update_by_id, update_by, remark
            host_ip = str(params[0])
            content_program_name = str(params[3])
            config_path = str(params[4])
            port = int(params[12]) if len(params) > 12 and params[12] is not None else None
            for item in self.tables.get("sys_supervisor_service", []):
                self._hydrate_supervisor_defaults(item)
                if item["host_ip"] != host_ip:
                    continue
                if item.get("content_program_name", item.get("program_name")) == content_program_name:
                    raise RuntimeError("duplicate content_program_name")
                if item["config_path"] == config_path:
                    raise RuntimeError("duplicate config_path")
                if port is not None and item["port"] is not None and int(item["port"]) == port:
                    raise RuntimeError("duplicate port")

            record_id = self.auto_increment["sys_supervisor_service"]
            self.auto_increment["sys_supervisor_service"] += 1
            self.tables.setdefault("sys_supervisor_service", []).append(
                self._build_supervisor_row(
                    record_id=record_id,
                    host_ip=host_ip,
                    job_name=str(params[1]) if params[1] is not None else None,
                    module_name=str(params[2]) if params[2] is not None else None,
                    program_name=content_program_name,
                    config_name=str(params[5]),
                    config_path=config_path,
                    file_name=str(params[5]),
                    content_program_name=content_program_name,
                    manage_mode=str(params[6]),
                    baseline_content=str(params[7]),
                    metadata_complete=bool(params[8]),
                    parse_warnings=str(params[9]),
                    java_path=str(params[10]) if len(params) > 10 and params[10] is not None else None,
                    active_profile=str(params[11]) if len(params) > 11 and params[11] is not None else None,
                    port=port,
                    jar_name=str(params[13]) if len(params) > 13 and params[13] is not None else None,
                    xms=str(params[14]) if len(params) > 14 and params[14] is not None else None,
                    xmx=str(params[15]) if len(params) > 15 and params[15] is not None else None,
                    run_user=str(params[16]) if len(params) > 16 and params[16] is not None else None,
                    create_by_id=int(params[17]) if len(params) > 17 else 0,
                    create_by=str(params[18]) if len(params) > 18 else "system",
                    update_by_id=int(params[19]) if len(params) > 19 else 0,
                    update_by=str(params[20]) if len(params) > 20 else "system",
                    remark=str(params[21]) if len(params) > 21 else "",
                )
            )
            cursor.lastrowid = record_id
            return 1

        # UPDATE with SET job_name, module_name, content_program_name (new pattern)
        if "UPDATE sys_supervisor_service SET job_name = %s, module_name = %s, content_program_name = %s" in normalized:
            if self.fail_next_supervisor_update:
                self.fail_next_supervisor_update = False
                raise RuntimeError("模拟 Supervisor 主数据更新失败")

            # Full update with runtime snapshot (update_service in mutation)
            if "status = %s, pid = %s, uptime = %s, status_sync_time = %s" in normalized:
                record_id = int(params[-1]) if params else 0
                if "WHERE id = %s" in normalized:
                    for item in self.tables.get("sys_supervisor_service", []):
                        if int(item["id"]) != record_id:
                            continue
                        self._hydrate_supervisor_defaults(item)
                        item["job_name"] = str(params[0]) if params[0] is not None else None
                        item["module_name"] = str(params[1]) if params[1] is not None else None
                        item["content_program_name"] = str(params[2])
                        item["config_path"] = str(params[3])
                        item["file_name"] = str(params[4])
                        item["manage_mode"] = str(params[5])
                        item["baseline_content"] = str(params[6])
                        item["metadata_complete"] = int(params[7])
                        item["parse_warnings"] = str(params[8])
                        item["java_path"] = str(params[9]) if params[9] is not None else None
                        item["active_profile"] = str(params[10]) if params[10] is not None else None
                        item["port"] = int(params[11]) if params[11] is not None else None
                        item["jar_name"] = str(params[12]) if params[12] is not None else None
                        item["xms"] = str(params[13]) if params[13] is not None else None
                        item["xmx"] = str(params[14]) if params[14] is not None else None
                        item["run_user"] = str(params[15]) if params[15] is not None else None
                        item["status"] = str(params[16])
                        item["pid"] = str(params[17]) if params[17] is not None else None
                        item["uptime"] = str(params[18]) if params[18] is not None else None
                        item["status_sync_time"] = str(params[19])
                        item["command"] = str(params[20]) if params[20] is not None else None
                        item["directory"] = str(params[21]) if params[21] is not None else None
                        item["stdout_logfile"] = str(params[22]) if params[22] is not None else None
                        item["has_backup"] = int(params[23])
                        item["config_content"] = str(params[24]) if params[24] is not None else None
                        item["backup_config_content"] = str(params[25]) if params[25] is not None else None
                        item["last_sync_at"] = str(params[26])
                        item["sync_status"] = str(params[27])
                        item["sync_error"] = str(params[28]) if params[28] is not None else None
                        item["update_by_id"] = int(params[29])
                        item["update_by"] = str(params[30])
                        item["remark"] = str(params[31])
                        return 1
                return 0

            # WHERE host_ip = %s AND content_program_name = %s (registry update)
            if "WHERE host_ip = %s AND content_program_name = %s" in normalized:
                target_host = str(params[-2])
                target_pn = str(params[-1])
                for item in self.tables.get("sys_supervisor_service", []):
                    if item["host_ip"] != target_host or item.get("content_program_name", item.get("program_name")) != target_pn:
                        continue
                    self._hydrate_supervisor_defaults(item)
                    item["job_name"] = str(params[0]) if params[0] is not None else None
                    item["module_name"] = str(params[1]) if params[1] is not None else None
                    item["content_program_name"] = str(params[2])
                    item["config_path"] = str(params[3])
                    item["file_name"] = str(params[4])
                    item["manage_mode"] = str(params[5])
                    item["baseline_content"] = str(params[6])
                    item["metadata_complete"] = int(params[7])
                    item["parse_warnings"] = str(params[8])
                    item["java_path"] = str(params[9]) if params[9] is not None else None
                    item["active_profile"] = str(params[10]) if params[10] is not None else None
                    item["port"] = int(params[11]) if params[11] is not None else None
                    item["jar_name"] = str(params[12]) if params[12] is not None else None
                    item["xms"] = str(params[13]) if params[13] is not None else None
                    item["xmx"] = str(params[14]) if params[14] is not None else None
                    item["run_user"] = str(params[15]) if params[15] is not None else None
                    item["update_by_id"] = int(params[16])
                    item["update_by"] = str(params[17])
                    item["remark"] = str(params[18])
                    return 1
                return 0

            # WHERE id = %s (upsert_imported update)
            if "WHERE id = %s" in normalized:
                record_id = int(params[-1])
                for item in self.tables.get("sys_supervisor_service", []):
                    if int(item["id"]) != record_id:
                        continue
                    self._hydrate_supervisor_defaults(item)
                    item["job_name"] = str(params[0]) if params[0] is not None else None
                    item["module_name"] = str(params[1]) if params[1] is not None else None
                    item["content_program_name"] = str(params[2])
                    item["config_path"] = str(params[3])
                    item["file_name"] = str(params[4])
                    item["manage_mode"] = str(params[5])
                    item["baseline_content"] = str(params[6])
                    item["metadata_complete"] = int(params[7])
                    item["parse_warnings"] = str(params[8])
                    item["java_path"] = str(params[9]) if params[9] is not None else None
                    item["active_profile"] = str(params[10]) if params[10] is not None else None
                    item["port"] = int(params[11]) if params[11] is not None else None
                    item["jar_name"] = str(params[12]) if params[12] is not None else None
                    item["xms"] = str(params[13]) if params[13] is not None else None
                    item["xmx"] = str(params[14]) if params[14] is not None else None
                    item["run_user"] = str(params[15]) if params[15] is not None else None
                    item["update_by_id"] = int(params[16])
                    item["update_by"] = str(params[17])
                    item["remark"] = str(params[18])
                    return 1
                return 0

        # Full sync update (supervisor_sync_service) — must check before the simpler status update
        if normalized.startswith(
            "UPDATE sys_supervisor_service SET status = %s, pid = %s, uptime = %s, status_sync_time = %s, command = %s,"
        ):
            (
                status,
                pid,
                uptime,
                status_sync_time,
                command,
                directory,
                stdout_logfile,
                job_name,
                module_name,
                java_path,
                active_profile,
                port,
                jar_name,
                xms,
                xmx,
                run_user,
                has_backup,
                config_content,
                backup_config_content,
                last_sync_at,
                sync_status,
                sync_error,
                host_ip,
                content_program_name,
            ) = params
            updated = 0
            for item in self.tables.get("sys_supervisor_service", []):
                self._hydrate_supervisor_defaults(item)
                if item["host_ip"] != host_ip or item.get("content_program_name", item.get("program_name")) != content_program_name:
                    continue
                item["status"] = str(status)
                item["pid"] = str(pid) if pid is not None else None
                item["uptime"] = str(uptime) if uptime is not None else None
                item["status_sync_time"] = str(status_sync_time)
                item["command"] = str(command) if command is not None else None
                item["directory"] = str(directory) if directory is not None else None
                item["stdout_logfile"] = str(stdout_logfile) if stdout_logfile is not None else None
                item["job_name"] = str(job_name) if job_name is not None else None
                item["module_name"] = str(module_name) if module_name is not None else None
                item["java_path"] = str(java_path) if java_path is not None else None
                item["active_profile"] = str(active_profile) if active_profile is not None else None
                item["port"] = int(port) if port is not None else None
                item["jar_name"] = str(jar_name) if jar_name is not None else None
                item["xms"] = str(xms) if xms is not None else None
                item["xmx"] = str(xmx) if xmx is not None else None
                item["run_user"] = str(run_user) if run_user is not None else None
                item["has_backup"] = int(has_backup)
                item["config_content"] = str(config_content) if config_content is not None else None
                item["backup_config_content"] = str(backup_config_content) if backup_config_content is not None else None
                item["last_sync_at"] = str(last_sync_at)
                item["sync_status"] = str(sync_status)
                item["sync_error"] = str(sync_error) if sync_error is not None else None
                updated += 1
            cursor.rowcount = updated
            return updated

        # Archive
        if normalized.startswith("UPDATE sys_supervisor_service SET is_archived = 1,"):
            archived_at, update_by_id, update_by, host_ip, content_program_name = params
            updated = 0
            for item in self.tables.get("sys_supervisor_service", []):
                self._hydrate_supervisor_defaults(item)
                if item["host_ip"] != host_ip or item.get("content_program_name", item.get("program_name")) != content_program_name:
                    continue
                item["is_archived"] = 1
                item["archived_at"] = str(archived_at)
                item["status"] = "STOPPED"
                item["pid"] = None
                item["uptime"] = None
                item["update_by_id"] = int(update_by_id)
                item["update_by"] = str(update_by)
                updated += 1
            cursor.rowcount = updated
            return updated

        # Simple status-only update (batch_update_status / update_single_status)
        if normalized.startswith("UPDATE sys_supervisor_service SET status = %s, pid = %s, uptime = %s, status_sync_time = %s") and "command = %s" not in normalized and "is_archived" not in normalized:
            status, pid, uptime, status_sync_time, host_ip, content_program_name = params
            updated = 0
            for item in self.tables.get("sys_supervisor_service", []):
                if item["host_ip"] != host_ip or item.get("content_program_name", item.get("program_name")) != content_program_name:
                    continue
                item["status"] = str(status)
                item["pid"] = str(pid) if pid is not None else None
                item["uptime"] = str(uptime) if uptime is not None else None
                item["status_sync_time"] = str(status_sync_time)
                updated += 1
            cursor.rowcount = updated
            return updated

        # Archive
        if normalized.startswith("UPDATE sys_supervisor_service SET is_archived = 0,"):
            restored_at, status, pid, uptime, host_ip, content_program_name = params
            updated = 0
            for item in self.tables.get("sys_supervisor_service", []):
                self._hydrate_supervisor_defaults(item)
                if item["host_ip"] != host_ip or item.get("content_program_name", item.get("program_name")) != content_program_name:
                    continue
                item["is_archived"] = 0
                item["restored_at"] = str(restored_at)
                item["status"] = str(status)
                item["pid"] = str(pid) if pid is not None else None
                item["uptime"] = str(uptime) if uptime is not None else None
                updated += 1
            cursor.rowcount = updated
            return updated

        # DELETE by host_ip and content_program_name
        if "DELETE FROM sys_supervisor_service WHERE host_ip = %s AND content_program_name = %s" in normalized:
            if self.fail_next_supervisor_delete:
                self.fail_next_supervisor_delete = False
                raise RuntimeError("模拟 Supervisor 主数据删除失败")
            host_ip = str(params[0])
            content_program_name = str(params[1])
            rows = self.tables.get("sys_supervisor_service", [])
            before = len(rows)
            self.tables["sys_supervisor_service"] = [
                item for item in rows
                if not (item["host_ip"] == host_ip and item.get("content_program_name", item.get("program_name")) == content_program_name)
            ]
            deleted = before - len(self.tables["sys_supervisor_service"])
            cursor.rowcount = deleted
            return deleted

        # DELETE by id
        if "DELETE FROM sys_supervisor_service WHERE id = %s" in normalized:
            if self.fail_next_supervisor_delete:
                self.fail_next_supervisor_delete = False
                raise RuntimeError("模拟 Supervisor 主数据删除失败")
            record_id = int(params[0])
            rows = self.tables.get("sys_supervisor_service", [])
            before = len(rows)
            self.tables["sys_supervisor_service"] = [item for item in rows if int(item["id"]) != record_id]
            deleted = before - len(self.tables["sys_supervisor_service"])
            cursor.rowcount = deleted
            return deleted

        # SELECT COUNT(*)
        if normalized.startswith("SELECT COUNT(*) AS cnt FROM sys_supervisor_service"):
            rows = list(self.tables.get("sys_supervisor_service", []))
            if "WHERE" in normalized:
                where_part = normalized.split("WHERE")[1].strip()
                rows = self._filter_supervisor_rows(rows, where_part, params)
            cursor.results = [{"cnt": len(rows)}]
            return len(rows)

        # Port conflict check SELECT
        if normalized.startswith("SELECT id, host_ip, content_program_name, config_path, port"):
            host_ip, port_val = params
            rows = [
                dict(item)
                for item in self.tables.get("sys_supervisor_service", [])
                if str(item.get("host_ip", "")) == str(host_ip)
                and str(item.get("port", "")) == str(port_val)
                and item.get("is_archived", 0) == 0
            ]
            cursor.results = rows
            return len(cursor.results)

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
def client(test_environment, fake_mysql, fake_supervisor):
    """创建测试客户端，每次更新模块级服务实例以使用当前测试配置。"""
    import app.api.supervisor as api_module
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

    get_settings.cache_clear()
    settings = get_settings()

    # 原地更新模块级服务实例，确保路由函数使用当前测试的配置路径
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

    from app.main import create_app
    app = create_app()
    return TestClient(app)
