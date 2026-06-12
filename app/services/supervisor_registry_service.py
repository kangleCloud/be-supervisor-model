"""Supervisor 主数据落库服务。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from uuid import uuid4

from app.core.config import Settings
from app.core.exceptions import ConfigAlreadyExistsError, ConfigNotFoundError, ParamError, PortConflictError
from app.core.security import ensure_safe_host, ensure_safe_program_name, ensure_valid_port, normalize_config_name, normalize_config_path
from app.database.bootstrap import transaction_context
from app.database.repositories.supervisor import SupervisorImportStagingRepository, SupervisorServiceRepository
from app.services.template_service import TemplateService


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
    """写入 Supervisor 主数据所需字段（仅保留真实字段，无镜像列）。"""

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
    java_path: str | None = None
    active_profile: str | None = None
    port: int | None = None
    jar_name: str | None = None
    xms: str | None = None
    xmx: str | None = None
    run_user: str | None = None


@dataclass(frozen=True)
class SupervisorRegistryRecord:
    """Supervisor 主数据记录（仅保留真实字段，无镜像列）。"""

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


@dataclass(frozen=True)
class ImportStagingRecord:
    """导入预检暂存记录。"""

    id: int
    batch_id: str
    host_ip: str
    operator_id: int
    operator_name: str
    config_path: str
    file_name: str
    content_program_name: str | None
    baseline_content: str | None
    metadata_complete: bool
    parse_warnings: tuple[str, ...]
    job_name: str | None
    module_name: str | None
    java_path: str | None
    active_profile: str | None
    port: int | None
    jar_name: str | None
    xms: str | None
    xmx: str | None
    run_user: str | None
    result: str
    message: str | None
    create_time: datetime | None


class SupervisorRegistryService:
    """负责 Supervisor 配置主数据的查询与新增。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.repository = SupervisorServiceRepository()

    async def get_by_content_program_name(self, host: str, content_program_name: str) -> SupervisorRegistryRecord:
        """按主机和 contentProgramName 查询单条记录。"""
        record = await self.get_by_content_program_name_optional(host, content_program_name)
        if record is None:
            raise ConfigNotFoundError(f"未找到服务 {ensure_safe_program_name(content_program_name)}")
        return record

    async def get_by_content_program_name_optional(self, host: str, content_program_name: str) -> SupervisorRegistryRecord | None:
        """按主机和 contentProgramName 查询单条记录，不存在时返回 None。"""
        safe_host = ensure_safe_host(host)
        safe_pn = ensure_safe_program_name(content_program_name)
        row = await self.repository.find_by_content_program_name(safe_host, safe_pn)
        return self._build_record(row) if row is not None else None

    async def get_by_config_path_optional(self, host: str, config_path: str) -> SupervisorRegistryRecord | None:
        """按主机和配置相对路径查询单条记录，不存在时返回 None。"""
        safe_host = ensure_safe_host(host)
        safe_config_path = normalize_config_path(config_path)
        row = await self.repository.find_by_config_path(safe_host, safe_config_path)
        return self._build_record(row) if row is not None else None

    async def search_page(
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
        if safe_status is not None and safe_status not in VALID_STATUS_VALUES:
            raise ParamError(f"status 只支持 {', '.join(sorted(VALID_STATUS_VALUES))}")
        if archived not in VALID_ARCHIVED_FILTERS:
            raise ParamError("archived 只支持 false / true / all")
        if page_size not in ALLOWED_PAGE_SIZES:
            raise ParamError("pageSize 只支持 10、20 或 50")

        safe_host = ensure_safe_host(host) if host else None
        rows, total, pages = await self.repository.search_page(
            host=safe_host,
            keyword=safe_keyword,
            status=safe_status,
            archived=archived,
            page=page,
            page_size=page_size,
        )
        return [self._build_record(row) for row in rows], total, pages

    async def create(
        self,
        data: SupervisorRegistryCreateData,
        *,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> SupervisorRegistryRecord:
        """新增一条 Supervisor 主数据记录。"""
        normalized = self._normalize_write_data(data)
        await self._ensure_no_conflict(normalized)
        row = await self.repository.create(self._build_write_payload(normalized, operator_id, operator_name, remark))
        return self._build_record(row)

    async def update(
        self,
        *,
        host: str,
        content_program_name: str,
        data: SupervisorRegistryCreateData,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> SupervisorRegistryRecord:
        """更新一条 Supervisor 主数据记录。"""
        safe_host = ensure_safe_host(host)
        safe_pn = ensure_safe_program_name(content_program_name)
        normalized = self._normalize_write_data(data)
        updated = await self.repository.update_by_host_and_program(
            safe_host,
            safe_pn,
            self._build_update_payload(normalized, operator_id, operator_name, remark),
        )
        if updated == 0:
            raise ConfigNotFoundError(f"未找到服务 {safe_pn}")
        return await self.get_by_content_program_name(normalized.host_ip, normalized.content_program_name)

    async def delete(
        self,
        *,
        host: str,
        content_program_name: str,
    ) -> SupervisorRegistryRecord:
        """删除一条 Supervisor 主数据记录，返回删除前的快照。"""
        record = await self.get_by_content_program_name(host, content_program_name)
        safe_host = ensure_safe_host(host)
        safe_pn = ensure_safe_program_name(content_program_name)
        await self.repository.delete_by_host_and_program(safe_host, safe_pn)
        return record

    async def mark_archived(
        self,
        *,
        host: str,
        content_program_name: str,
        archived_at: datetime,
        operator_id: int,
        operator_name: str,
    ) -> None:
        """标记服务为已归档状态。"""
        safe_host = ensure_safe_host(host)
        safe_pn = ensure_safe_program_name(content_program_name)
        await self.repository.update_by_host_and_program(
            safe_host,
            safe_pn,
            {
                "is_archived": True,
                "archived_at": archived_at,
                "status": "STOPPED",
                "pid": None,
                "uptime": None,
                "update_by_id": operator_id,
                "update_by": operator_name,
            },
        )

    async def mark_restored(
        self,
        *,
        host: str,
        content_program_name: str,
        restored_at: datetime,
        status: str,
        pid: str | None,
        uptime: str | None,
    ) -> None:
        """标记服务为已还原状态。"""
        safe_host = ensure_safe_host(host)
        safe_pn = ensure_safe_program_name(content_program_name)
        await self.repository.update_by_host_and_program(
            safe_host,
            safe_pn,
            {
                "is_archived": False,
                "restored_at": restored_at,
                "status": status,
                "pid": pid,
                "uptime": uptime,
            },
        )

    async def batch_update_status(
        self,
        host: str,
        status_entries: list[tuple[str, str, str | None, str | None]],
    ) -> tuple[int, int]:
        """批量刷新主数据状态快照，返回 (updated_count, missing_count)。"""
        safe_host = ensure_safe_host(host)
        now_value = datetime.now()
        updated = 0
        missing_names: set[str] = set()
        for content_pn, state, pid, uptime in status_entries:
            safe_pn = ensure_safe_program_name(content_pn)
            count = await self.repository.update_by_host_and_program(
                safe_host,
                safe_pn,
                {
                    "status": state,
                    "pid": pid,
                    "uptime": uptime,
                    "status_sync_time": now_value,
                },
            )
            if count > 0:
                updated += 1
            else:
                missing_names.add(content_pn)
        return updated, len(missing_names)

    async def update_single_status(
        self,
        host: str,
        content_program_name: str,
        state: str,
        pid: str | None,
        uptime: str | None,
    ) -> None:
        """刷新单服务状态快照。"""
        safe_host = ensure_safe_host(host)
        safe_pn = ensure_safe_program_name(content_program_name)
        await self.repository.update_by_host_and_program(
            safe_host,
            safe_pn,
            {
                "status": state,
                "pid": pid,
                "uptime": uptime,
                "status_sync_time": datetime.now(),
            },
        )

    async def update_runtime_snapshot(
        self,
        host: str,
        content_program_name: str,
        *,
        status: str,
        pid: str | None,
        uptime: str | None,
    ) -> None:
        """更新单服务运行时快照，供运行操作与还原流程复用。"""
        await self.update_single_status(host, content_program_name, status, pid, uptime)

    async def update_detail_sync_snapshot(
        self,
        host: str,
        content_program_name: str,
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
        safe_pn = ensure_safe_program_name(content_program_name)
        current_sync_time = sync_time or datetime.now()
        await self.repository.update_by_host_and_program(
            safe_host,
            safe_pn,
            {
                "status": status,
                "pid": pid,
                "uptime": uptime,
                "status_sync_time": current_sync_time,
                "command": command,
                "directory": directory,
                "stdout_logfile": stdout_logfile,
                "job_name": job_name,
                "module_name": module_name,
                "java_path": java_path,
                "active_profile": active_profile,
                "port": port,
                "jar_name": jar_name,
                "xms": xms,
                "xmx": xmx,
                "run_user": run_user,
                "has_backup": has_backup,
                "config_content": config_content,
                "backup_config_content": backup_config_content,
                "last_sync_at": current_sync_time,
                "sync_status": sync_status,
                "sync_error": sync_error,
            },
        )

    async def ensure_can_save(
        self,
        data: SupervisorRegistryCreateData,
        *,
        exclude_record_id: int | None = None,
    ) -> None:
        """校验同主机下 contentProgramName、configPath、port 不冲突。"""
        if data.port is not None:
            ensure_valid_port(data.port)
        normalized = self._normalize_write_data(data)
        await self._ensure_no_conflict(normalized, exclude_record_id=exclude_record_id)

    async def ensure_can_create(self, data: SupervisorRegistryCreateData) -> None:
        """兼容创建流程的冲突校验。"""
        await self.ensure_can_save(data)

    async def _ensure_no_conflict(
        self,
        data: SupervisorRegistryCreateData,
        *,
        exclude_record_id: int | None = None,
        using_db=None,
    ) -> None:
        """校验同主机下 contentProgramName、configPath、port 不冲突，由 create/update/导入提交统一调用。"""
        safe_host = ensure_safe_host(data.host_ip)
        safe_pn = ensure_safe_program_name(data.content_program_name)
        existing = await self.repository.find_by_content_program_name(safe_host, safe_pn, using_db=using_db)
        if existing is not None and int(existing.id) != (exclude_record_id or 0):
            if str(existing.config_path) != data.config_path:
                raise ConfigAlreadyExistsError(f"服务已存在: {data.content_program_name}")
            raise ConfigAlreadyExistsError(f"配置文件已存在: {data.config_path}")
        path_exists = await self.repository.find_by_config_path(safe_host, data.config_path, using_db=using_db)
        if path_exists is not None and int(path_exists.id) != (exclude_record_id or 0):
            if str(path_exists.content_program_name) != data.content_program_name:
                raise ConfigAlreadyExistsError(f"配置文件已存在: {data.config_path}")
            raise ConfigAlreadyExistsError(f"服务已存在: {data.content_program_name}")
        if data.port is not None:
            conflict = await self.repository.find_port_conflict(
                safe_host,
                data.port,
                exclude_record_id=exclude_record_id,
                using_db=using_db,
            )
            if conflict is not None:
                raise PortConflictError(
                    "端口冲突",
                    [{
                        "host": str(conflict.host_ip),
                        "contentProgramName": str(conflict.content_program_name),
                        "configPath": str(conflict.config_path),
                        "port": int(conflict.port),
                    }],
                )

    async def upsert_imported(
        self,
        data: SupervisorRegistryCreateData,
        *,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> tuple[SupervisorRegistryRecord, bool]:
        """按 host + configPath 幂等写入只读导入快照。"""
        normalized = self._normalize_write_data(data)
        existing_by_path = await self.repository.find_by_config_path(normalized.host_ip, normalized.config_path)
        if existing_by_path is None:
            created = await self.repository.create(self._build_write_payload(normalized, operator_id, operator_name, remark))
            return self._build_record(created), True
        await self.repository.update_by_id(
            int(existing_by_path.id),
            self._build_update_payload(normalized, operator_id, operator_name, remark),
        )
        refreshed = await self.repository.find_by_config_path(normalized.host_ip, normalized.config_path)
        return self._build_record(refreshed), False

    async def plan_import_upsert(
        self,
        data: SupervisorRegistryCreateData,
    ) -> tuple[SupervisorRegistryCreateData, SupervisorRegistryRecord | None]:
        """校验导入快照写库规则，并返回规范化数据及同路径现存记录。"""
        normalized = self._normalize_write_data(data)
        if normalized.manage_mode != MANAGE_MODE_IMPORTED_READONLY:
            raise ParamError("导入快照必须使用 IMPORTED_READONLY 模式")

        existing_by_path = await self.get_by_config_path_optional(normalized.host_ip, normalized.config_path)
        existing_by_pn = await self.get_by_content_program_name_optional(normalized.host_ip, normalized.content_program_name)
        if existing_by_pn is not None and existing_by_pn.config_path != normalized.config_path:
            raise ConfigAlreadyExistsError(f"服务已存在: {existing_by_pn.content_program_name}")
        return normalized, existing_by_path

    async def update_service(
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
        await self.repository.update_by_id(
            record_id,
            {
                **self._build_update_payload(normalized, operator_id, operator_name, remark),
                "status": status,
                "pid": pid,
                "uptime": uptime,
                "status_sync_time": datetime.now(),
                "command": command,
                "directory": directory,
                "stdout_logfile": stdout_logfile,
                "has_backup": has_backup,
                "config_content": config_content,
                "backup_config_content": backup_config_content,
                "last_sync_at": datetime.now(),
                "sync_status": sync_status,
                "sync_error": sync_error,
            },
        )

    async def delete_service(self, record_id: int) -> None:
        """按主键删除单条服务记录。"""
        await self.repository.delete_by_id(record_id)

    def _normalize_write_data(self, data: SupervisorRegistryCreateData) -> SupervisorRegistryCreateData:
        safe_host = ensure_safe_host(data.host_ip)
        if data.manage_mode not in ALLOWED_MANAGE_MODES:
            raise ParamError("manageMode 非法")

        safe_content_pn = ensure_safe_program_name(data.content_program_name)
        safe_file_name = normalize_config_name(data.file_name, safe_content_pn)
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
            content_program_name=safe_content_pn,
            baseline_content=data.baseline_content or "",
            parse_warnings=tuple(data.parse_warnings),
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

    def _build_write_payload(
        self,
        data: SupervisorRegistryCreateData,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> dict[str, object]:
        return {
            "host_ip": data.host_ip,
            "job_name": data.job_name,
            "module_name": data.module_name,
            "content_program_name": data.content_program_name,
            "config_path": data.config_path,
            "file_name": data.file_name,
            "manage_mode": data.manage_mode,
            "baseline_content": data.baseline_content,
            "metadata_complete": bool(data.metadata_complete),
            "parse_warnings": self._serialize_parse_warnings(data.parse_warnings),
            "java_path": data.java_path,
            "active_profile": data.active_profile,
            "port": data.port,
            "jar_name": data.jar_name,
            "xms": data.xms,
            "xmx": data.xmx,
            "run_user": data.run_user,
            "create_by_id": operator_id,
            "create_by": operator_name,
            "update_by_id": operator_id,
            "update_by": operator_name,
            "remark": remark,
        }

    def _build_update_payload(
        self,
        data: SupervisorRegistryCreateData,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> dict[str, object]:
        return {
            "job_name": data.job_name,
            "module_name": data.module_name,
            "content_program_name": data.content_program_name,
            "config_path": data.config_path,
            "file_name": data.file_name,
            "manage_mode": data.manage_mode,
            "baseline_content": data.baseline_content,
            "metadata_complete": bool(data.metadata_complete),
            "parse_warnings": self._serialize_parse_warnings(data.parse_warnings),
            "java_path": data.java_path,
            "active_profile": data.active_profile,
            "port": data.port,
            "jar_name": data.jar_name,
            "xms": data.xms,
            "xmx": data.xmx,
            "run_user": data.run_user,
            "update_by_id": operator_id,
            "update_by": operator_name,
            "remark": remark,
        }

    def _build_record(self, row) -> SupervisorRegistryRecord:
        return SupervisorRegistryRecord(
            id=int(row.id),
            host_ip=str(row.host_ip),
            config_path=str(row.config_path),
            file_name=str(row.file_name),
            content_program_name=str(row.content_program_name),
            manage_mode=str(row.manage_mode),
            baseline_content=str(row.baseline_content or ""),
            metadata_complete=bool(row.metadata_complete),
            parse_warnings=self._deserialize_parse_warnings(row.parse_warnings),
            job_name=self._to_optional_str(row.job_name),
            module_name=self._to_optional_str(row.module_name),
            java_path=self._to_optional_str(row.java_path),
            active_profile=self._to_optional_str(row.active_profile),
            port=int(row.port) if row.port is not None else None,
            jar_name=self._to_optional_str(row.jar_name),
            xms=self._to_optional_str(row.xms),
            xmx=self._to_optional_str(row.xmx),
            run_user=self._to_optional_str(row.run_user),
            status=str(row.status or "UNKNOWN"),
            pid=self._to_optional_str(row.pid),
            uptime=self._to_optional_str(row.uptime),
            status_sync_time=row.status_sync_time,
            command=self._to_optional_str(row.command),
            directory=self._to_optional_str(row.directory),
            stdout_logfile=self._to_optional_str(row.stdout_logfile),
            has_backup=bool(row.has_backup),
            config_content=self._to_optional_str(row.config_content),
            backup_config_content=self._to_optional_str(row.backup_config_content),
            last_sync_at=row.last_sync_at,
            sync_status=str(row.sync_status or "UNKNOWN"),
            sync_error=self._to_optional_str(row.sync_error),
            is_archived=bool(row.is_archived),
            archived_at=row.archived_at,
            restored_at=row.restored_at,
            update_time=row.update_time,
        )

    @staticmethod
    def _to_optional_str(value: object) -> str | None:
        if value in (None, ""):
            return None
        return str(value)


class ImportStagingService:
    """负责导入预检暂存表的写入与原子提交。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.repository = SupervisorImportStagingRepository()

    @staticmethod
    def create_batch_id() -> str:
        """生成新的导入批次标识。"""
        return str(uuid4())

    async def clear_operator_host_batches(self, *, host_ip: str, operator_id: int) -> None:
        """清理同一操作人同一主机的历史暂存批次，避免前端误提旧批次。"""
        await self.repository.clear_operator_host_batches(host_ip=ensure_safe_host(host_ip), operator_id=operator_id)

    async def delete_expired_batches(self, *, ttl_hours: int = 24) -> None:
        """清理过期暂存数据，避免暂存表长期堆积。"""
        expire_before = datetime.now() - timedelta(hours=ttl_hours)
        await self.repository.delete_expired_batches(expire_before=expire_before)

    async def insert_batch(
        self,
        *,
        batch_id: str,
        host_ip: str,
        operator_id: int,
        operator_name: str,
        items: list[dict[str, object]],
    ) -> None:
        """批量写入暂存记录。"""
        await self.repository.insert_batch(
            batch_id=batch_id,
            host_ip=host_ip,
            operator_id=operator_id,
            operator_name=operator_name,
            items=items,
        )

    async def get_batch(
        self,
        *,
        batch_id: str,
        host_ip: str,
        operator_id: int,
    ) -> list[ImportStagingRecord]:
        """按 batchId + host + operator 查询暂存记录。"""
        rows = await self.repository.get_batch(batch_id=batch_id, host_ip=ensure_safe_host(host_ip), operator_id=operator_id)
        return [self._build_record(row) for row in rows]

    async def get_latest_batch(
        self,
        *,
        host_ip: str,
        operator_id: int,
    ) -> list[ImportStagingRecord]:
        """按主机和当前用户读取最近一批暂存数据。"""
        rows = await self.repository.get_latest_batch(
            host_ip=ensure_safe_host(host_ip),
            operator_id=operator_id,
        )
        return [self._build_record(row) for row in rows]

    def build_report_from_records(
        self,
        *,
        host: str,
        batch_id: str,
        records: list[ImportStagingRecord],
    ) -> dict[str, object]:
        """把暂存记录组装成前端可直接展示的批次结果。"""
        items = [self._build_item_payload(record) for record in records]
        summary = {
            "planned": sum(1 for record in records if record.result != "SKIPPED"),
            "imported": sum(1 for record in records if record.result == "IMPORTED"),
            "updated": sum(1 for record in records if record.result == "UPDATED"),
            "skipped": sum(1 for record in records if record.result == "SKIPPED"),
        }
        return {
            "host": host,
            "batchId": batch_id,
            "summary": summary,
            "items": items,
        }

    async def delete_batch(self, batch_id: str) -> None:
        """删除指定批次的暂存记录。"""
        await self.repository.delete_batch(batch_id)

    def has_skipped(self, records: list[ImportStagingRecord]) -> bool:
        """检查批次中是否存在 SKIPPED 记录。"""
        return any(r.result == "SKIPPED" for r in records)

    def build_create_data(self, record: ImportStagingRecord) -> SupervisorRegistryCreateData:
        """将暂存记录转为正式表写入草稿。"""
        if record.content_program_name is None:
            raise ParamError(f"导入批次存在无效记录，缺少 contentProgramName: {record.config_path}")
        return SupervisorRegistryCreateData(
            host_ip=record.host_ip,
            config_path=record.config_path,
            file_name=record.file_name,
            content_program_name=record.content_program_name,
            manage_mode=MANAGE_MODE_IMPORTED_READONLY,
            baseline_content=record.baseline_content or "",
            metadata_complete=record.metadata_complete,
            parse_warnings=record.parse_warnings,
            job_name=record.job_name,
            module_name=record.module_name,
            java_path=record.java_path,
            active_profile=record.active_profile,
            port=record.port,
            jar_name=record.jar_name,
            xms=record.xms,
            xmx=record.xmx,
            run_user=record.run_user,
        )

    async def commit_batch(
        self,
        *,
        batch_id: str,
        host_ip: str,
        operator_id: int,
        operator_name: str,
        registry_service: SupervisorRegistryService,
        remark: str,
    ) -> list[tuple[SupervisorRegistryRecord, bool, SupervisorRegistryRecord | None]]:
        """把一个暂存批次原子提交到正式表，并在成功后清空暂存数据。"""
        safe_host = ensure_safe_host(host_ip)
        async with transaction_context() as connection:
            staged_rows = await self.repository.get_batch(
                batch_id=batch_id,
                host_ip=safe_host,
                operator_id=operator_id,
                using_db=connection,
            )
            staged_records = [self._build_record(row) for row in staged_rows]
            if not staged_records:
                raise ConfigNotFoundError("未找到导入批次")
            if self.has_skipped(staged_records):
                raise ConfigAlreadyExistsError("当前导入批次存在跳过项，不能提交")

            batch_program_paths: dict[str, str] = {}
            batch_config_paths: set[str] = set()
            for staged_record in staged_records:
                if staged_record.content_program_name is None:
                    raise ParamError(f"导入批次存在无效记录，缺少 contentProgramName: {staged_record.config_path}")
                duplicate_path = batch_program_paths.get(staged_record.content_program_name)
                if duplicate_path is not None and duplicate_path != staged_record.config_path:
                    raise ConfigAlreadyExistsError(
                        f"同一批次存在重复 contentProgramName: {staged_record.content_program_name}"
                    )
                if staged_record.config_path in batch_config_paths:
                    raise ConfigAlreadyExistsError(f"同一批次存在重复 configPath: {staged_record.config_path}")
                batch_program_paths[staged_record.content_program_name] = staged_record.config_path
                batch_config_paths.add(staged_record.config_path)

            results: list[tuple[SupervisorRegistryRecord, bool, SupervisorRegistryRecord | None]] = []
            for staged_record in staged_records:
                normalized = registry_service._normalize_write_data(self.build_create_data(staged_record))
                existing_by_path_model = await registry_service.repository.find_by_config_path(
                    normalized.host_ip,
                    normalized.config_path,
                    using_db=connection,
                )
                existing_by_path = (
                    registry_service._build_record(existing_by_path_model)
                    if existing_by_path_model is not None
                    else None
                )
                existing_by_pn_model = await registry_service.repository.find_by_content_program_name(
                    normalized.host_ip,
                    normalized.content_program_name,
                    using_db=connection,
                )
                if (
                    existing_by_pn_model is not None
                    and str(existing_by_pn_model.config_path) != normalized.config_path
                ):
                    raise ConfigAlreadyExistsError(f"服务已存在: {existing_by_pn_model.content_program_name}")

                if existing_by_path_model is None:
                    created = await registry_service.repository.create(
                        registry_service._build_write_payload(normalized, operator_id, operator_name, remark),
                        using_db=connection,
                    )
                    results.append((registry_service._build_record(created), True, None))
                    continue

                await registry_service.repository.update_by_id(
                    int(existing_by_path_model.id),
                    registry_service._build_update_payload(normalized, operator_id, operator_name, remark),
                    using_db=connection,
                )
                refreshed = await registry_service.repository.find_by_config_path(
                    normalized.host_ip,
                    normalized.config_path,
                    using_db=connection,
                )
                results.append((registry_service._build_record(refreshed), False, existing_by_path))

            await self.repository.delete_batch_scope(
                batch_id=batch_id,
                host_ip=safe_host,
                operator_id=operator_id,
                using_db=connection,
            )
        return results

    @staticmethod
    def _build_record(row) -> ImportStagingRecord:
        parse_warnings = ()
        if row.parse_warnings:
            parse_warnings = tuple(json.loads(row.parse_warnings))
        return ImportStagingRecord(
            id=int(row.id),
            batch_id=str(row.batch_id),
            host_ip=str(row.host_ip),
            operator_id=int(row.operator_id),
            operator_name=str(row.operator_name),
            config_path=str(row.config_path),
            file_name=str(row.file_name),
            content_program_name=_optional_str(row.content_program_name),
            baseline_content=_optional_str(row.baseline_content),
            metadata_complete=bool(row.metadata_complete),
            parse_warnings=parse_warnings,
            job_name=_optional_str(row.job_name),
            module_name=_optional_str(row.module_name),
            java_path=_optional_str(row.java_path),
            active_profile=_optional_str(row.active_profile),
            port=int(row.port) if row.port is not None else None,
            jar_name=_optional_str(row.jar_name),
            xms=_optional_str(row.xms),
            xmx=_optional_str(row.xmx),
            run_user=_optional_str(row.run_user),
            result=str(row.result),
            message=_optional_str(row.message),
            create_time=row.create_time,
        )

    @staticmethod
    def _build_item_payload(record: ImportStagingRecord) -> dict[str, object]:
        """统一恢复暂存记录的前端字段，避免恢复接口和预检接口字段漂移。"""
        return {
            "configPath": record.config_path,
            "fileName": record.file_name,
            "contentProgramName": record.content_program_name,
            "jobName": record.job_name,
            "moduleName": record.module_name,
            "javaPath": record.java_path,
            "active": record.active_profile,
            "port": record.port,
            "jarName": record.jar_name,
            "xms": record.xms,
            "xmx": record.xmx,
            "user": record.run_user,
            "manageMode": MANAGE_MODE_IMPORTED_READONLY,
            "metadataComplete": record.metadata_complete,
            "parseWarnings": list(record.parse_warnings),
            "result": record.result,
            "message": record.message or "",
        }


def _optional_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
