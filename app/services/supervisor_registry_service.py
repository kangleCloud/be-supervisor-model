"""Supervisor 主数据落库服务。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePosixPath

from app.core.config import Settings
from app.core.database import get_connection
from app.core.exceptions import ConfigAlreadyExistsError, ConfigNotFoundError, ParamError, PortConflictError
from app.core.security import ensure_safe_host, ensure_safe_program_name, ensure_valid_port, normalize_config_name, normalize_config_path


MANAGE_MODE_TEMPLATE_MANAGED = "TEMPLATE_MANAGED"
MANAGE_MODE_IMPORTED_READONLY = "IMPORTED_READONLY"
ALLOWED_MANAGE_MODES = {MANAGE_MODE_TEMPLATE_MANAGED, MANAGE_MODE_IMPORTED_READONLY}

VALID_STATUS_VALUES = frozenset({
    "RUNNING", "STOPPED", "FATAL", "BACKOFF", "STARTING", "STOPPING", "EXITED", "UNKNOWN",
})
VALID_ARCHIVED_FILTERS = frozenset({"false", "true", "all"})

ALLOWED_PAGE_SIZES = frozenset({10, 20, 50})


@dataclass(frozen=True)
class SupervisorRegistryCreateData:
    """写入 Supervisor 主数据所需字段。"""

    host_ip: str
    config_path: str
    file_name: str
    content_program_name: str
    manage_mode: str
    baseline_content: str
    metadata_complete: bool
    parse_warnings: tuple[str, ...] = ()
    job_name: str | None = None
    module_name: str | None = None
    program_name: str | None = None
    config_name: str | None = None
    java_path: str | None = None
    active_profile: str | None = None
    port: int | None = None
    jar_name: str | None = None
    xms: str | None = None
    xmx: str | None = None
    run_user: str | None = None


@dataclass(frozen=True)
class SupervisorRegistryRecord:
    """Supervisor 主数据记录。"""

    id: int
    host_ip: str
    config_path: str
    file_name: str
    content_program_name: str
    manage_mode: str
    baseline_content: str
    metadata_complete: bool
    parse_warnings: tuple[str, ...]
    job_name: str | None
    module_name: str | None
    program_name: str
    config_name: str
    java_path: str | None
    active_profile: str | None
    port: int | None
    jar_name: str | None
    xms: str | None
    xmx: str | None
    run_user: str | None
    status: str
    pid: str | None
    uptime: str | None
    status_sync_time: datetime | None
    command: str | None
    directory: str | None
    stdout_logfile: str | None
    has_backup: bool
    config_content: str | None
    backup_config_content: str | None
    last_sync_at: datetime | None
    sync_status: str
    sync_error: str | None
    is_archived: bool
    archived_at: datetime | None
    restored_at: datetime | None
    update_time: datetime | None


class SupervisorRegistryService:
    """负责 Supervisor 配置主数据的查询与新增。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def list_by_host(self, host: str) -> list[SupervisorRegistryRecord]:
        """按主机查询已纳管的 Supervisor 服务。"""
        safe_host = ensure_safe_host(host)
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, host_ip, config_path, file_name, content_program_name, manage_mode,
                           baseline_content, metadata_complete, parse_warnings,
                           job_name, module_name, program_name, config_name,
                           java_path, active_profile, port, jar_name, xms, xmx, run_user,
                           status, pid, uptime, status_sync_time,
                           command, directory, stdout_logfile, has_backup,
                           config_content, backup_config_content, last_sync_at, sync_status, sync_error,
                           is_archived, archived_at, restored_at, update_time
                    FROM sys_supervisor_service
                    WHERE host_ip = %s
                    ORDER BY id ASC
                    """,
                    (safe_host,),
                )
                rows = cursor.fetchall()
        return [self._build_record(row) for row in rows]

    def get_by_program_name(self, host: str, program_name: str) -> SupervisorRegistryRecord:
        """按主机和 programName 查询单条记录。"""
        record = self.get_by_program_name_optional(host, program_name)
        if record is None:
            raise ConfigNotFoundError(f"未找到服务 {ensure_safe_program_name(program_name)}")
        return record

    def get_by_program_name_optional(self, host: str, program_name: str) -> SupervisorRegistryRecord | None:
        """按主机和 programName 查询单条记录，不存在时返回 None。"""
        safe_host = ensure_safe_host(host)
        safe_program_name = ensure_safe_program_name(program_name)
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, host_ip, config_path, file_name, content_program_name, manage_mode,
                           baseline_content, metadata_complete, parse_warnings,
                           job_name, module_name, program_name, config_name,
                           java_path, active_profile, port, jar_name, xms, xmx, run_user,
                           status, pid, uptime, status_sync_time,
                           command, directory, stdout_logfile, has_backup,
                           config_content, backup_config_content, last_sync_at, sync_status, sync_error,
                           is_archived, archived_at, restored_at, update_time
                    FROM sys_supervisor_service
                    WHERE host_ip = %s AND program_name = %s
                    LIMIT 1
                    """,
                    (safe_host, safe_program_name),
                )
                row = cursor.fetchone()
        return self._build_record(row) if row is not None else None

    def get_by_config_path_optional(self, host: str, config_path: str) -> SupervisorRegistryRecord | None:
        """按主机和配置相对路径查询单条记录，不存在时返回 None。"""
        safe_host = ensure_safe_host(host)
        safe_config_path = normalize_config_path(config_path)
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, host_ip, config_path, file_name, content_program_name, manage_mode,
                           baseline_content, metadata_complete, parse_warnings,
                           job_name, module_name, program_name, config_name,
                           java_path, active_profile, port, jar_name, xms, xmx, run_user,
                           status, pid, uptime, status_sync_time,
                           command, directory, stdout_logfile, has_backup,
                           config_content, backup_config_content, last_sync_at, sync_status, sync_error,
                           is_archived, archived_at, restored_at, update_time
                    FROM sys_supervisor_service
                    WHERE host_ip = %s AND config_path = %s
                    LIMIT 1
                    """,
                    (safe_host, safe_config_path),
                )
                row = cursor.fetchone()
        return self._build_record(row) if row is not None else None

    def search_page(
        self,
        *,
        host: str | None = None,
        keyword: str | None = None,
        status: str | None = None,
        archived: str = "false",
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[SupervisorRegistryRecord], int, int]:
        """分页查询服务列表，返回 (records, total, pages)。"""
        safe_keyword = keyword.strip() if keyword else None
        safe_status = status.strip().upper() if status else None
        safe_archived = archived.strip().lower()
        if safe_status is not None and safe_status not in VALID_STATUS_VALUES:
            raise ParamError(f"status 只支持 {', '.join(sorted(VALID_STATUS_VALUES))}")
        if safe_archived not in VALID_ARCHIVED_FILTERS:
            raise ParamError("archived 只支持 false、true 或 all")

        where_clauses: list[str] = []
        params: list[object] = []

        if host:
            safe_host = ensure_safe_host(host)
            where_clauses.append("host_ip = %s")
            params.append(safe_host)

        if safe_keyword:
            keyword_pattern = f"%{safe_keyword}%"
            where_clauses.append(
                "(program_name LIKE %s OR config_name LIKE %s "
                "OR job_name LIKE %s OR module_name LIKE %s "
                "OR CAST(port AS CHAR) LIKE %s)"
            )
            params.extend([keyword_pattern] * 5)

        if safe_status:
            where_clauses.append("status = %s")
            params.append(safe_status)

        if safe_archived != "all":
            # 列表默认只看未归档记录，避免历史归档项干扰日常运维视图。
            where_clauses.append("is_archived = %s")
            params.append(1 if safe_archived == "true" else 0)

        where_sql = ""
        if where_clauses:
            where_sql = f"WHERE {' AND '.join(where_clauses)}"

        offset = (page - 1) * page_size

        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS cnt FROM sys_supervisor_service {where_sql}",
                    tuple(params),
                )
                total = int(cursor.fetchone()["cnt"])

            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, host_ip, config_path, file_name, content_program_name, manage_mode,
                           baseline_content, metadata_complete, parse_warnings,
                           job_name, module_name, program_name, config_name,
                           java_path, active_profile, port, jar_name, xms, xmx, run_user,
                           status, pid, uptime, status_sync_time,
                           is_archived, archived_at, restored_at, update_time
                    FROM sys_supervisor_service
                    {where_sql}
                    ORDER BY update_time DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    tuple(params) + (page_size, offset),
                )
                rows = cursor.fetchall()

        pages = 0 if total == 0 else math.ceil(total / page_size)
        return [self._build_record(row) for row in rows], total, pages

    def update_runtime_snapshot(
        self,
        host: str,
        program_name: str,
        *,
        status: str,
        pid: str | None,
        uptime: str | None,
    ) -> None:
        """更新单服务运行时快照，供运行操作与还原流程复用。"""
        safe_host = ensure_safe_host(host)
        safe_program_name = ensure_safe_program_name(program_name)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sys_supervisor_service
                    SET status = %s,
                        pid = %s,
                        uptime = %s,
                        status_sync_time = %s
                    WHERE host_ip = %s AND program_name = %s
                    """,
                    (status, pid, uptime, now_str, safe_host, safe_program_name),
                )
            connection.commit()

    def update_detail_sync_snapshot(
        self,
        host: str,
        program_name: str,
        *,
        sync_time: datetime | None = None,
        status: str,
        pid: str | None,
        uptime: str | None,
        command: str | None,
        directory: str | None,
        stdout_logfile: str | None,
        job_name: str | None,
        module_name: str | None,
        java_path: str | None,
        active_profile: str | None,
        port: int | None,
        jar_name: str | None,
        xms: str | None,
        xmx: str | None,
        run_user: str | None,
        has_backup: bool,
        config_content: str | None,
        backup_config_content: str | None,
        sync_status: str,
        sync_error: str | None,
    ) -> None:
        """写入单服务详情同步快照，保持详情接口始终只查数据库。"""
        safe_host = ensure_safe_host(host)
        safe_program_name = ensure_safe_program_name(program_name)
        current_sync_time = sync_time or datetime.now()
        now_str = current_sync_time.strftime("%Y-%m-%d %H:%M:%S")
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sys_supervisor_service
                    SET status = %s,
                        pid = %s,
                        uptime = %s,
                        status_sync_time = %s,
                        command = %s,
                        directory = %s,
                        stdout_logfile = %s,
                        job_name = %s,
                        module_name = %s,
                        java_path = %s,
                        active_profile = %s,
                        port = %s,
                        jar_name = %s,
                        xms = %s,
                        xmx = %s,
                        run_user = %s,
                        has_backup = %s,
                        config_content = %s,
                        backup_config_content = %s,
                        last_sync_at = %s,
                        sync_status = %s,
                        sync_error = %s
                    WHERE host_ip = %s AND program_name = %s
                    """,
                    (
                        status,
                        pid,
                        uptime,
                        now_str,
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
                        int(has_backup),
                        config_content,
                        backup_config_content,
                        now_str,
                        sync_status,
                        sync_error,
                        safe_host,
                        safe_program_name,
                    ),
                )
            connection.commit()

    def mark_archived(
        self,
        host: str,
        program_name: str,
        *,
        operator_id: int,
        operator_name: str,
        archived_at: datetime,
    ) -> None:
        """把服务标记为已归档，并同步停止态快照。"""
        safe_host = ensure_safe_host(host)
        safe_program_name = ensure_safe_program_name(program_name)
        archived_at_str = archived_at.strftime("%Y-%m-%d %H:%M:%S")
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sys_supervisor_service
                    SET is_archived = 1,
                        archived_at = %s,
                        status = 'STOPPED',
                        pid = NULL,
                        uptime = NULL,
                        status_sync_time = %s,
                        update_by_id = %s,
                        update_by = %s
                    WHERE host_ip = %s AND program_name = %s
                    """,
                    (archived_at_str, archived_at_str, operator_id, operator_name, safe_host, safe_program_name),
                )
            connection.commit()

    def mark_restored(
        self,
        host: str,
        program_name: str,
        *,
        operator_id: int,
        operator_name: str,
        restored_at: datetime,
        status: str,
        pid: str | None,
        uptime: str | None,
    ) -> None:
        """把服务标记为未归档，并同步还原后的状态快照。"""
        safe_host = ensure_safe_host(host)
        safe_program_name = ensure_safe_program_name(program_name)
        restored_at_str = restored_at.strftime("%Y-%m-%d %H:%M:%S")
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sys_supervisor_service
                    SET is_archived = 0,
                        restored_at = %s,
                        status = %s,
                        pid = %s,
                        uptime = %s,
                        status_sync_time = %s,
                        update_by_id = %s,
                        update_by = %s
                    WHERE host_ip = %s AND program_name = %s
                    """,
                    (
                        restored_at_str,
                        status,
                        pid,
                        uptime,
                        restored_at_str,
                        operator_id,
                        operator_name,
                        safe_host,
                        safe_program_name,
                    ),
                )
            connection.commit()

    def batch_update_status(
        self,
        host: str,
        status_entries: list[tuple[str, str, str | None, str | None]],
    ) -> tuple[int, int]:
        """批量刷新主数据状态快照，返回 (updated_count, missing_count)。"""
        safe_host = ensure_safe_host(host)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated = 0
        missing_names: set[str] = set()

        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                for program_name, state, pid, uptime in status_entries:
                    cursor.execute(
                        """
                        UPDATE sys_supervisor_service
                        SET status = %s,
                            pid = %s,
                            uptime = %s,
                            status_sync_time = %s
                        WHERE host_ip = %s AND program_name = %s
                        """,
                        (state, pid, uptime, now_str, safe_host, program_name),
                    )
                    if cursor.rowcount > 0:
                        updated += 1
                    else:
                        missing_names.add(program_name)
            connection.commit()

        return updated, len(missing_names)

    def ensure_can_save(self, data: SupervisorRegistryCreateData, *, exclude_record_id: int | None = None) -> None:
        """校验同主机下 programName、configPath、port 不冲突，可排除当前记录。"""
        normalized = self._normalize_write_data(data)
        if normalized.port is not None:
            ensure_valid_port(normalized.port)
        for record in self.list_by_host(normalized.host_ip):
            if exclude_record_id is not None and record.id == exclude_record_id:
                continue
            if record.program_name == normalized.program_name:
                raise ConfigAlreadyExistsError(f"服务已存在: {record.program_name}")
            if record.config_path == normalized.config_path:
                raise ConfigAlreadyExistsError(f"配置文件已存在: {record.config_path}")
            if normalized.port is not None and record.port is not None and record.port == normalized.port:
                raise PortConflictError(
                    "端口冲突",
                    [
                        {
                            "host": record.host_ip,
                            "programName": record.program_name,
                            "configName": record.config_name,
                            "configPath": record.config_path,
                            "port": record.port,
                        }
                    ],
                )

    def ensure_can_create(self, data: SupervisorRegistryCreateData) -> None:
        """兼容创建流程的冲突校验。"""
        self.ensure_can_save(data)

    def create(
        self,
        data: SupervisorRegistryCreateData,
        *,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> SupervisorRegistryRecord:
        """新增一条 Supervisor 主数据记录。"""
        normalized = self._normalize_write_data(data)
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO sys_supervisor_service(
                        host_ip, job_name, module_name, program_name, config_name,
                        config_path, file_name, content_program_name, manage_mode,
                        baseline_content, metadata_complete, parse_warnings,
                        java_path, active_profile, port, jar_name, xms, xmx, run_user,
                        create_by_id, create_by, update_by_id, update_by, remark
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    self._build_write_params(normalized, operator_id, operator_name, remark),
                )
                record_id = int(cursor.lastrowid)
            connection.commit()
        return self._build_record_from_data(record_id, normalized)

    def update_service(
        self,
        *,
        record_id: int,
        data: SupervisorRegistryCreateData,
        operator_id: int,
        operator_name: str,
        remark: str,
        status: str,
        pid: str | None,
        uptime: str | None,
        command: str | None,
        directory: str | None,
        stdout_logfile: str | None,
        has_backup: bool,
        config_content: str | None,
        backup_config_content: str | None,
        sync_status: str,
        sync_error: str | None,
    ) -> None:
        """更新单服务主数据与最新写入后的现场快照。"""
        normalized = self._normalize_write_data(data)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sys_supervisor_service
                    SET job_name = %s,
                        module_name = %s,
                        program_name = %s,
                        config_name = %s,
                        config_path = %s,
                        file_name = %s,
                        content_program_name = %s,
                        manage_mode = %s,
                        baseline_content = %s,
                        metadata_complete = %s,
                        parse_warnings = %s,
                        java_path = %s,
                        active_profile = %s,
                        port = %s,
                        jar_name = %s,
                        xms = %s,
                        xmx = %s,
                        run_user = %s,
                        status = %s,
                        pid = %s,
                        uptime = %s,
                        status_sync_time = %s,
                        command = %s,
                        directory = %s,
                        stdout_logfile = %s,
                        has_backup = %s,
                        config_content = %s,
                        backup_config_content = %s,
                        last_sync_at = %s,
                        sync_status = %s,
                        sync_error = %s,
                        update_by_id = %s,
                        update_by = %s,
                        remark = %s
                    WHERE id = %s
                    """,
                    (
                        normalized.job_name,
                        normalized.module_name,
                        normalized.program_name,
                        normalized.config_name,
                        normalized.config_path,
                        normalized.file_name,
                        normalized.content_program_name,
                        normalized.manage_mode,
                        normalized.baseline_content,
                        int(normalized.metadata_complete),
                        self._serialize_parse_warnings(normalized.parse_warnings),
                        normalized.java_path,
                        normalized.active_profile,
                        normalized.port,
                        normalized.jar_name,
                        normalized.xms,
                        normalized.xmx,
                        normalized.run_user,
                        status,
                        pid,
                        uptime,
                        now_str,
                        command,
                        directory,
                        stdout_logfile,
                        int(has_backup),
                        config_content,
                        backup_config_content,
                        now_str,
                        sync_status,
                        sync_error,
                        operator_id,
                        operator_name,
                        remark,
                        record_id,
                    ),
                )
            connection.commit()

    def delete_service(self, record_id: int) -> None:
        """按主键删除单条服务记录。"""
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM sys_supervisor_service WHERE id = %s", (record_id,))
            connection.commit()

    def upsert_imported(
        self,
        data: SupervisorRegistryCreateData,
        *,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> tuple[SupervisorRegistryRecord, bool]:
        """按 host + configPath 幂等写入只读导入快照。"""
        normalized, existing_by_path = self.plan_import_upsert(data)

        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                if existing_by_path is None:
                    cursor.execute(
                        """
                        INSERT INTO sys_supervisor_service(
                            host_ip, job_name, module_name, program_name, config_name,
                            config_path, file_name, content_program_name, manage_mode,
                            baseline_content, metadata_complete, parse_warnings,
                            java_path, active_profile, port, jar_name, xms, xmx, run_user,
                            create_by_id, create_by, update_by_id, update_by, remark
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        """,
                        self._build_write_params(normalized, operator_id, operator_name, remark),
                    )
                    record_id = int(cursor.lastrowid)
                    connection.commit()
                    return self._build_record_from_data(record_id, normalized), True

                cursor.execute(
                    """
                    UPDATE sys_supervisor_service
                    SET job_name = %s,
                        module_name = %s,
                        program_name = %s,
                        config_name = %s,
                        config_path = %s,
                        file_name = %s,
                        content_program_name = %s,
                        manage_mode = %s,
                        baseline_content = %s,
                        metadata_complete = %s,
                        parse_warnings = %s,
                        java_path = %s,
                        active_profile = %s,
                        port = %s,
                        jar_name = %s,
                        xms = %s,
                        xmx = %s,
                        run_user = %s,
                        update_by_id = %s,
                        update_by = %s,
                        remark = %s
                    WHERE id = %s
                    """,
                    (
                        normalized.job_name,
                        normalized.module_name,
                        normalized.program_name,
                        normalized.config_name,
                        normalized.config_path,
                        normalized.file_name,
                        normalized.content_program_name,
                        normalized.manage_mode,
                        normalized.baseline_content,
                        int(normalized.metadata_complete),
                        self._serialize_parse_warnings(normalized.parse_warnings),
                        normalized.java_path,
                        normalized.active_profile,
                        normalized.port,
                        normalized.jar_name,
                        normalized.xms,
                        normalized.xmx,
                        normalized.run_user,
                        operator_id,
                        operator_name,
                        remark,
                        existing_by_path.id,
                    ),
                )
            connection.commit()
        return self._build_record_from_data(existing_by_path.id, normalized), False

    def plan_import_upsert(
        self,
        data: SupervisorRegistryCreateData,
    ) -> tuple[SupervisorRegistryCreateData, SupervisorRegistryRecord | None]:
        """校验导入快照写库规则，并返回规范化数据及同路径现存记录。"""
        normalized = self._normalize_write_data(data)
        if normalized.manage_mode != MANAGE_MODE_IMPORTED_READONLY:
            raise ParamError("导入快照必须使用 IMPORTED_READONLY 模式")

        existing_by_path = self.get_by_config_path_optional(normalized.host_ip, normalized.config_path)
        existing_by_program = self.get_by_program_name_optional(normalized.host_ip, normalized.program_name)
        if existing_by_program is not None and existing_by_program.config_path != normalized.config_path:
            raise ConfigAlreadyExistsError(f"服务已存在: {existing_by_program.program_name}")
        return normalized, existing_by_path

    def _normalize_write_data(self, data: SupervisorRegistryCreateData) -> SupervisorRegistryCreateData:
        safe_host = ensure_safe_host(data.host_ip)
        if data.manage_mode not in ALLOWED_MANAGE_MODES:
            raise ParamError("manageMode 非法")

        safe_program_name = ensure_safe_program_name(data.content_program_name)
        safe_file_name = normalize_config_name(data.file_name, safe_program_name)
        safe_config_path = normalize_config_path(data.config_path)
        if PurePosixPath(safe_config_path).name != safe_file_name:
            raise ParamError("configPath 与 fileName 不一致")

        if data.port is not None:
            ensure_valid_port(data.port)

        return replace(
            data,
            host_ip=safe_host,
            config_path=safe_config_path,
            file_name=safe_file_name,
            content_program_name=safe_program_name,
            program_name=safe_program_name,
            config_name=safe_file_name,
            baseline_content=data.baseline_content or "",
            parse_warnings=tuple(data.parse_warnings),
        )

    @staticmethod
    def _build_write_params(
        data: SupervisorRegistryCreateData,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> tuple[object, ...]:
        return (
            data.host_ip,
            data.job_name,
            data.module_name,
            data.program_name,
            data.config_name,
            data.config_path,
            data.file_name,
            data.content_program_name,
            data.manage_mode,
            data.baseline_content,
            int(data.metadata_complete),
            SupervisorRegistryService._serialize_parse_warnings(data.parse_warnings),
            data.java_path,
            data.active_profile,
            data.port,
            data.jar_name,
            data.xms,
            data.xmx,
            data.run_user,
            operator_id,
            operator_name,
            operator_id,
            operator_name,
            remark,
        )

    @staticmethod
    def _serialize_parse_warnings(parse_warnings: tuple[str, ...]) -> str:
        return json.dumps(list(parse_warnings), ensure_ascii=False)

    @staticmethod
    def _deserialize_parse_warnings(value: object) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        try:
            raw_list = json.loads(str(value))
        except json.JSONDecodeError:
            return (str(value),)
        if not isinstance(raw_list, list):
            return (str(value),)
        return tuple(str(item) for item in raw_list)

    def _build_record_from_data(self, record_id: int, data: SupervisorRegistryCreateData) -> SupervisorRegistryRecord:
        return SupervisorRegistryRecord(
            id=record_id,
            host_ip=data.host_ip,
            config_path=data.config_path,
            file_name=data.file_name,
            content_program_name=data.content_program_name,
            manage_mode=data.manage_mode,
            baseline_content=data.baseline_content,
            metadata_complete=bool(data.metadata_complete),
            parse_warnings=tuple(data.parse_warnings),
            job_name=data.job_name,
            module_name=data.module_name,
            program_name=str(data.program_name),
            config_name=str(data.config_name),
            java_path=data.java_path,
            active_profile=data.active_profile,
            port=data.port,
            jar_name=data.jar_name,
            xms=data.xms,
            xmx=data.xmx,
            run_user=data.run_user,
            status="UNKNOWN",
            pid=None,
            uptime=None,
            status_sync_time=None,
            command=None,
            directory=None,
            stdout_logfile=None,
            has_backup=False,
            config_content=None,
            backup_config_content=None,
            last_sync_at=None,
            sync_status="UNKNOWN",
            sync_error=None,
            is_archived=False,
            archived_at=None,
            restored_at=None,
            update_time=None,
        )

    def _build_record(self, row: dict[str, object]) -> SupervisorRegistryRecord:
        return SupervisorRegistryRecord(
            id=int(row["id"]),
            host_ip=str(row["host_ip"]),
            config_path=str(row["config_path"]),
            file_name=str(row["file_name"]),
            content_program_name=str(row["content_program_name"]),
            manage_mode=str(row["manage_mode"]),
            baseline_content=str(row["baseline_content"] or ""),
            metadata_complete=bool(row["metadata_complete"]),
            parse_warnings=self._deserialize_parse_warnings(row["parse_warnings"]),
            job_name=self._to_optional_str(row["job_name"]),
            module_name=self._to_optional_str(row["module_name"]),
            program_name=str(row["program_name"]),
            config_name=str(row["config_name"]),
            java_path=self._to_optional_str(row["java_path"]),
            active_profile=self._to_optional_str(row["active_profile"]),
            port=int(row["port"]) if row["port"] is not None else None,
            jar_name=self._to_optional_str(row["jar_name"]),
            xms=self._to_optional_str(row["xms"]),
            xmx=self._to_optional_str(row["xmx"]),
            run_user=self._to_optional_str(row["run_user"]),
            status=str(row.get("status", "UNKNOWN")),
            pid=self._to_optional_str(row.get("pid")),
            uptime=self._to_optional_str(row.get("uptime")),
            status_sync_time=row.get("status_sync_time"),
            command=self._to_optional_str(row.get("command")),
            directory=self._to_optional_str(row.get("directory")),
            stdout_logfile=self._to_optional_str(row.get("stdout_logfile")),
            has_backup=bool(row.get("has_backup", 0)),
            config_content=self._to_optional_str(row.get("config_content")),
            backup_config_content=self._to_optional_str(row.get("backup_config_content")),
            last_sync_at=row.get("last_sync_at"),
            sync_status=str(row.get("sync_status", "UNKNOWN")),
            sync_error=self._to_optional_str(row.get("sync_error")),
            is_archived=bool(row.get("is_archived", 0)),
            archived_at=row.get("archived_at"),
            restored_at=row.get("restored_at"),
            update_time=row.get("update_time"),
        )

    @staticmethod
    def _to_optional_str(value: object) -> str | None:
        if value in (None, ""):
            return None
        return str(value)
