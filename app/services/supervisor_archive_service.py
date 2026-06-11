"""Supervisor 归档与还原服务。"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.async_utils import run_blocking
from app.core.exceptions import AppError, ArchiveStateError
from app.schemas.supervisor import ArchiveActionResponse
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.supervisor_registry_service import SupervisorRegistryRecord, SupervisorRegistryService
from app.services.supervisor_service import SupervisorService


LOGGER = logging.getLogger(__name__)


def _format_datetime_text(value: object) -> str | None:
    """兼容真实 MySQL datetime 和测试夹具中的字符串时间。"""
    if value in (None, ""):
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


class SupervisorArchiveService:
    """处理归档/还原与远端配置文件联动。"""

    def __init__(
        self,
        host_service: HostService,
        config_file_service: ConfigFileService,
        registry_service: SupervisorRegistryService,
        supervisor_service: SupervisorService,
    ):
        self.host_service = host_service
        self.config_file_service = config_file_service
        self.registry_service = registry_service
        self.supervisor_service = supervisor_service

    async def archive_service(
        self,
        host: str,
        program_name: str,
        *,
        operator_id: int,
        operator_name: str,
    ) -> dict[str, object]:
        """归档服务并联动远端 .ini 现场。"""
        record = await self._load_record(host, program_name)
        if record.is_archived:
            raise ArchiveStateError("服务已归档，无需重复归档")

        LOGGER.info("归档服务：目标主机=%s，服务名称=%s", host, record.content_program_name)
        stop_result = await run_blocking(self.supervisor_service.stop, host, record.content_program_name, True)
        backup_result = await run_blocking(self.config_file_service.backup_config_by_config_path, host, record.config_path)
        delete_result = await run_blocking(self.config_file_service.delete_config_by_config_path, host, record.config_path)
        reread_result = await run_blocking(self.supervisor_service.reread, host)
        update_result = await run_blocking(self.supervisor_service.update, host)

        archived_at = datetime.now()
        await self.registry_service.mark_archived(
            host=host,
            content_program_name=record.content_program_name,
            operator_id=operator_id,
            operator_name=operator_name,
            archived_at=archived_at,
        )
        LOGGER.info("归档服务成功：目标主机=%s，服务名称=%s", host, record.content_program_name)
        return ArchiveActionResponse(
            host=host,
            contentProgramName=record.content_program_name,
            isArchived=True,
            archivedAt=archived_at.strftime("%Y-%m-%d %H:%M:%S"),
            restoredAt=_format_datetime_text(record.restored_at),
            status="STOPPED",
            commandResult={
                "stop": stop_result,
                "reread": reread_result,
                "update": update_result,
            },
            fileResult={
                "backup": backup_result,
                "delete": delete_result,
            },
        ).model_dump(by_alias=True)

    async def restore_service(
        self,
        host: str,
        program_name: str,
        *,
        operator_id: int,
        operator_name: str,
    ) -> dict[str, object]:
        """还原归档服务的配置，但不自动启动。"""
        del operator_id, operator_name
        record = await self._load_record(host, program_name)
        if not record.is_archived:
            raise ArchiveStateError("服务未归档，无需还原")

        LOGGER.info("还原服务：目标主机=%s，服务名称=%s", host, record.content_program_name)
        restore_result = await run_blocking(self.config_file_service.restore_config_by_config_path, host, record.config_path)
        reread_result = await run_blocking(self.supervisor_service.reread, host)
        update_result = await run_blocking(self.supervisor_service.update, host)
        status, pid, uptime = await self._query_runtime_snapshot(host, record.content_program_name)

        restored_at = datetime.now()
        await self.registry_service.mark_restored(
            host=host,
            content_program_name=record.content_program_name,
            restored_at=restored_at,
            status=status,
            pid=pid,
            uptime=uptime,
        )
        LOGGER.info("还原服务成功：目标主机=%s，服务名称=%s，状态=%s", host, record.content_program_name, status)
        return ArchiveActionResponse(
            host=host,
            contentProgramName=record.content_program_name,
            isArchived=False,
            archivedAt=_format_datetime_text(record.archived_at),
            restoredAt=restored_at.strftime("%Y-%m-%d %H:%M:%S"),
            status=status,
            commandResult={
                "reread": reread_result,
                "update": update_result,
            },
            fileResult={
                "restore": restore_result,
            },
        ).model_dump(by_alias=True)

    async def _load_record(self, host: str, program_name: str) -> SupervisorRegistryRecord:
        await run_blocking(self.host_service.get_host, host)
        return await self.registry_service.get_by_content_program_name(host, program_name)

    async def _query_runtime_snapshot(self, host: str, program_name: str) -> tuple[str, str | None, str | None]:
        # 还原后只同步现场状态，不自动执行 start，查不到就回写 UNKNOWN。
        try:
            status_entries = await run_blocking(self.supervisor_service.status, host, program_name)
        except AppError:
            status_entries = []
        entry = status_entries[0] if status_entries else None
        if entry is None:
            return "UNKNOWN", None, None
        return entry.state, entry.pid, entry.uptime
