"""Supervisor 运行操作服务。"""
from __future__ import annotations

import logging

from app.core.async_utils import run_blocking
from app.core.exceptions import AppError, ArchivedServiceOperationError
from app.schemas.supervisor import RuntimeActionResponse
from app.services.host_service import HostService
from app.services.supervisor_registry_service import SupervisorRegistryRecord, SupervisorRegistryService
from app.services.supervisor_service import SupervisorService


LOGGER = logging.getLogger(__name__)


class SupervisorRuntimeService:
    """处理启动、停止、重启等运行操作。"""

    def __init__(
        self,
        host_service: HostService,
        registry_service: SupervisorRegistryService,
        supervisor_service: SupervisorService,
    ):
        self.host_service = host_service
        self.registry_service = registry_service
        self.supervisor_service = supervisor_service

    async def start_service(self, host: str, program_name: str) -> dict[str, object]:
        """启动单个服务。"""
        return await self._run_action(host, program_name, action="start")

    async def stop_service(self, host: str, program_name: str) -> dict[str, object]:
        """停止单个服务。"""
        return await self._run_action(host, program_name, action="stop")

    async def restart_service(self, host: str, program_name: str) -> dict[str, object]:
        """重启单个服务。"""
        return await self._run_action(host, program_name, action="restart")

    async def _run_action(self, host: str, program_name: str, *, action: str) -> dict[str, object]:
        record = await self._load_active_record(host, program_name)
        action_label = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
        LOGGER.info("%s服务：目标主机=%s，服务名称=%s", action_label, host, record.content_program_name)

        if action == "start":
            command_result = await run_blocking(self.supervisor_service.start, host, record.content_program_name)
            fallback_status = "UNKNOWN"
        elif action == "stop":
            command_result = await run_blocking(self.supervisor_service.stop, host, record.content_program_name)
            fallback_status = "STOPPED"
        else:
            command_result = await run_blocking(self.supervisor_service.restart, host, record.content_program_name)
            fallback_status = "UNKNOWN"

        status, pid, uptime = await self._refresh_runtime_snapshot(
            host,
            record.content_program_name,
            fallback_status=fallback_status,
        )
        LOGGER.info("%s服务成功：目标主机=%s，服务名称=%s，状态=%s", action_label, host, record.content_program_name, status)
        return RuntimeActionResponse(
            host=host,
            contentProgramName=record.content_program_name,
            action=action,
            status=status,
            commandResult=command_result,
        ).model_dump(by_alias=True)

    async def _load_active_record(self, host: str, program_name: str) -> SupervisorRegistryRecord:
        await run_blocking(self.host_service.get_host, host)
        record = await self.registry_service.get_by_content_program_name(host, program_name)
        if record.is_archived:
            LOGGER.warning("服务已归档，禁止运行操作：目标主机=%s，服务名称=%s", host, record.content_program_name)
            raise ArchivedServiceOperationError()
        return record

    async def _refresh_runtime_snapshot(
        self,
        host: str,
        program_name: str,
        *,
        fallback_status: str,
    ) -> tuple[str, str | None, str | None]:
        # 运行命令完成后立即回读单服务状态；取不到时按既定兜底状态回写数据库。
        try:
            status_entries = await run_blocking(self.supervisor_service.status, host, program_name)
        except AppError:
            status_entries = []

        entry = status_entries[0] if status_entries else None
        status = entry.state if entry is not None else fallback_status
        pid = entry.pid if entry is not None else None
        uptime = entry.uptime if entry is not None else None
        await self.registry_service.update_runtime_snapshot(
            host,
            program_name,
            status=status,
            pid=pid,
            uptime=uptime,
        )
        return status, pid, uptime
