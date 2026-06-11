"""Supervisor 业务编排服务。"""
from __future__ import annotations

import logging

from app.core.exceptions import AppError
from app.schemas.supervisor import (
    PagedServiceResponse,
    ServiceCreateRequest,
    ServiceUpdateRequest,
    ServiceListQuery,
    ServiceListRecord,
    StatusRefreshResponse,
    SupervisorImportRequest,
)
from app.services.auth_service import AuthenticatedUser
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.supervisor_import_service import SupervisorImportService
from app.services.supervisor_archive_service import SupervisorArchiveService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_detail_service import SupervisorDetailService
from app.services.supervisor_registry_service import (
    SupervisorRegistryRecord,
    SupervisorRegistryService,
)
from app.services.supervisor_mutation_service import SupervisorMutationService
from app.services.supervisor_runtime_service import SupervisorRuntimeService
from app.services.supervisor_service import SupervisorService
from app.services.supervisor_sync_service import SupervisorSyncService
from app.services.template_service import TemplateService


LOGGER = logging.getLogger(__name__)
FILE_STATE_MATCH = "MATCH"


def _format_datetime_text(value: object) -> str | None:
    """兼容真实 MySQL datetime 和测试夹具中的字符串时间。"""
    if value in (None, ""):
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


class SupervisorManager:
    """聚合 API 所需的业务能力。"""

    def __init__(
        self,
        host_service: HostService,
        template_service: TemplateService,
        config_file_service: ConfigFileService,
        port_check_service: PortCheckService,
        supervisor_service: SupervisorService,
        registry_service: SupervisorRegistryService,
        import_service: SupervisorImportService,
        detail_service: SupervisorDetailService,
        mutation_service: SupervisorMutationService,
        runtime_service: SupervisorRuntimeService,
        archive_service: SupervisorArchiveService,
        sync_service: SupervisorSyncService,
    ):
        self.host_service = host_service
        self.template_service = template_service
        self.config_file_service = config_file_service
        self.port_check_service = port_check_service
        self.supervisor_service = supervisor_service
        self.registry_service = registry_service
        self.import_service = import_service
        self.detail_service = detail_service
        self.mutation_service = mutation_service
        self.runtime_service = runtime_service
        self.archive_orchestrator = archive_service
        self.sync_service = sync_service

    def list_hosts(self) -> list[dict[str, object]]:
        """返回允许的主机列表。"""
        return self.host_service.list_hosts()

    def list_services_page(self, query: ServiceListQuery) -> dict[str, object]:
        """纯数据库分页查询服务列表，不触发任何远端命令。"""
        LOGGER.info(
            "查询服务列表：目标主机=%s，关键字=%s，状态=%s，归档筛选=%s，当前页=%s，每页条数=%s",
            query.host, query.keyword, query.status, query.archived, query.page, query.page_size,
        )
        records, total, pages = self.registry_service.search_page(
            host=query.host,
            keyword=query.keyword,
            status=query.status,
            archived=query.archived,
            page=query.page,
            page_size=query.page_size,
        )
        result = PagedServiceResponse(
            records=[ServiceListRecord.from_record(r) for r in records],
            page=query.page,
            pageSize=query.page_size,
            total=total,
            pages=pages,
        )
        return result.model_dump(by_alias=True)

    def get_service_detail(self, host: str, program_name: str) -> dict[str, object]:
        """详情默认只读数据库快照，不隐式触发远端同步。"""
        return self.detail_service.get_service_detail(host, program_name)

    def create_service(self, payload: ServiceCreateRequest, current_user) -> dict[str, object]:
        """新增服务并在远端和数据库中同步落地。"""
        return self.mutation_service.create_service(payload, current_user)

    def update_service(
        self,
        host: str,
        program_name: str,
        payload: ServiceUpdateRequest,
        current_user: AuthenticatedUser,
    ) -> dict[str, object]:
        """修改单个服务。"""
        return self.mutation_service.update_service(host, program_name, payload, current_user)

    def delete_service(self, host: str, program_name: str, current_user: AuthenticatedUser) -> dict[str, object]:
        """删除单个服务。"""
        return self.mutation_service.delete_service(host, program_name, current_user)

    def refresh_status(self, host: str) -> dict[str, object]:
        """对指定主机执行一次 supervisorctl status 并批量刷新数据库状态快照。"""
        LOGGER.info("刷新服务状态：目标主机=%s", host)
        host_config = self.host_service.get_host(host)
        status_entries = self.supervisor_service.status(host)
        status_tuples = [
            (entry.program_name, entry.state, entry.pid, entry.uptime)
            for entry in status_entries
        ]
        updated, missing = self.registry_service.batch_update_status(host, status_tuples)
        LOGGER.info("刷新服务状态成功：目标主机=%s，更新条数=%s，未匹配条数=%s", host, updated, missing)
        return StatusRefreshResponse(
            host=host_config.ip,
            total=len(status_entries),
            updated=updated,
            missing=missing,
        ).model_dump(by_alias=True)

    def import_services(
        self,
        payload: SupervisorImportRequest,
        current_user: AuthenticatedUser,
    ) -> dict[str, object]:
        """执行初始化导入，并返回面向前端的逐文件结果。"""
        report = self.import_service.execute(
            host=payload.host,
            mode=payload.mode,
            operator_id=current_user.user_id,
            operator_name=current_user.username,
            recursive=True,
        )
        return report.to_dict()

    def sync_service_detail(self, host: str, program_name: str, current_user: AuthenticatedUser) -> dict[str, object]:
        """显式同步单服务远端状态与配置快照。"""
        return self.sync_service.sync_service(host, program_name)

    def start_service(self, host: str, program_name: str, current_user: AuthenticatedUser) -> dict[str, object]:
        """启动单个服务并刷新数据库状态快照。"""
        return self.runtime_service.start_service(host, program_name)

    def stop_service(self, host: str, program_name: str, current_user: AuthenticatedUser) -> dict[str, object]:
        """停止单个服务并刷新数据库状态快照。"""
        return self.runtime_service.stop_service(host, program_name)

    def restart_service(self, host: str, program_name: str, current_user: AuthenticatedUser) -> dict[str, object]:
        """重启单个服务并刷新数据库状态快照。"""
        return self.runtime_service.restart_service(host, program_name)

    def archive_service(self, host: str, program_name: str, current_user: AuthenticatedUser) -> dict[str, object]:
        """归档服务并联动远端配置文件现场。"""
        return self.archive_orchestrator.archive_service(
            host,
            program_name,
            operator_id=current_user.user_id,
            operator_name=current_user.username,
        )

    def restore_service(self, host: str, program_name: str, current_user: AuthenticatedUser) -> dict[str, object]:
        """还原归档服务配置，但不自动启动。"""
        return self.archive_orchestrator.restore_service(
            host,
            program_name,
            operator_id=current_user.user_id,
            operator_name=current_user.username,
        )
