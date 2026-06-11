"""Supervisor 详情查询服务。"""
from __future__ import annotations

from app.core.async_utils import run_blocking
from app.schemas.supervisor import ServiceDetailResponse
from app.services.host_service import HostService
from app.services.supervisor_registry_service import SupervisorRegistryService


class SupervisorDetailService:
    """详情只查数据库快照，避免打开页面就触发远端命令。"""

    def __init__(
        self,
        host_service: HostService,
        registry_service: SupervisorRegistryService,
    ):
        self.host_service = host_service
        self.registry_service = registry_service

    async def get_service_detail(self, host: str, program_name: str) -> dict[str, object]:
        """返回数据库中的单服务详情快照。"""
        host_config = await run_blocking(self.host_service.get_host, host)
        record = await self.registry_service.get_by_content_program_name(host, program_name)
        return ServiceDetailResponse.from_record(record, host_name=host_config.name).model_dump(by_alias=True)
