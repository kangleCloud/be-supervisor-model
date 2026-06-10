"""Supervisor 业务编排服务。"""
from __future__ import annotations

import logging

from app.core.exceptions import AppError, InternalError, ParamError
from app.schemas.supervisor import (
    PagedServiceResponse,
    ServiceCreateRequest,
    ServiceListQuery,
    ServiceListRecord,
    StatusRefreshResponse,
    SupervisorImportRequest,
)
from app.services.auth_service import AuthenticatedUser
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.supervisor_import_service import SupervisorImportService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_registry_service import (
    MANAGE_MODE_IMPORTED_READONLY,
    MANAGE_MODE_TEMPLATE_MANAGED,
    SupervisorRegistryCreateData,
    SupervisorRegistryRecord,
    SupervisorRegistryService,
)
from app.services.supervisor_service import SupervisorService
from app.services.template_service import TemplateService


LOGGER = logging.getLogger(__name__)
FILE_STATE_MATCH = "MATCH"
FILE_STATE_MISSING = "MISSING"
FILE_STATE_MISMATCH = "MISMATCH"


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
    ):
        self.host_service = host_service
        self.template_service = template_service
        self.config_file_service = config_file_service
        self.port_check_service = port_check_service
        self.supervisor_service = supervisor_service
        self.registry_service = registry_service
        self.import_service = import_service

    def list_hosts(self) -> list[dict[str, object]]:
        """返回允许的主机列表。"""
        return self.host_service.list_hosts()

    def list_services_page(self, query: ServiceListQuery) -> dict[str, object]:
        """纯数据库分页查询服务列表，不触发任何远端命令。"""
        LOGGER.info(
            "查询服务列表：目标主机=%s，关键字=%s，状态=%s，当前页=%s，每页条数=%s",
            query.host, query.keyword, query.status, query.page, query.page_size,
        )
        records, total, pages = self.registry_service.search_page(
            host=query.host,
            keyword=query.keyword,
            status=query.status,
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
        """返回纳管服务详情、期望配置与远端漂移信息。"""
        self.host_service.get_host(host)
        record = self.registry_service.get_by_program_name(host, program_name)
        expected_content = self._render_expected_content(record)
        remote_config = self.config_file_service.read_raw_config_optional_by_config_path(host, record.config_path)
        status_entries = self.supervisor_service.status(host, record.program_name)
        status = status_entries[0].to_dict() if status_entries else None
        file_state = self._resolve_file_state(expected_content, remote_config.content if remote_config else None)
        payload = self._build_service_payload(record, status=status, file_state=file_state)
        payload["expectedContent"] = expected_content
        if file_state == FILE_STATE_MISMATCH and remote_config is not None:
            payload["remoteContent"] = remote_config.content
        return payload

    def create_service(self, payload: ServiceCreateRequest, current_user) -> dict[str, object]:
        """新增服务并在远端和数据库中同步落地。"""
        # 当前项目已收紧约束：远端 ansible 主机只允许读，新增只能发生在 local 主机。
        self.host_service.ensure_mutation_allowed(payload.host, "当前项目禁止修改远端配置文件")
        rendered = self.template_service.render(payload)
        registry_data = SupervisorRegistryCreateData(
            host_ip=payload.host,
            job_name=payload.job_name,
            module_name=payload.module_name,
            program_name=rendered.program_name,
            config_name=rendered.config_name,
            config_path=rendered.config_name,
            file_name=rendered.config_name,
            content_program_name=rendered.program_name,
            manage_mode=MANAGE_MODE_TEMPLATE_MANAGED,
            baseline_content=rendered.content,
            metadata_complete=True,
            parse_warnings=(),
            java_path=payload.java_path,
            active_profile=payload.active,
            port=payload.port,
            jar_name=payload.jar_name or self.template_service.build_default_jar_name(payload.module_name),
            xms=payload.xms,
            xmx=payload.xmx,
            run_user=payload.user,
        )
        self.registry_service.ensure_can_create(registry_data)
        self.config_file_service.ensure_not_exists(payload.host, rendered.config_name, rendered.program_name)
        self.port_check_service.ensure_no_conflict(payload.host, payload.port)

        self.config_file_service.write_config(payload.host, rendered.config_name, rendered.content, rendered.program_name)
        self.supervisor_service.reread(payload.host)
        self.supervisor_service.update(payload.host)

        try:
            record = self.registry_service.create(
                registry_data,
                operator_id=current_user.user_id,
                operator_name=current_user.username,
                remark="Supervisor 服务配置",
            )
        except Exception as exc:
            rollback_result = self._rollback_remote_create(payload.host, rendered.config_name, rendered.program_name)
            if isinstance(exc, AppError):
                raise
            LOGGER.exception("新增服务写库失败：目标主机=%s，configName=%s", payload.host, rendered.config_name, exc_info=exc)
            raise InternalError("新增服务写库失败", rollback_result) from exc

        # 新增后不再实时查远端状态；数据库 record 已默认 status='UNKNOWN'
        return self._build_service_payload(record, status=None, file_state=FILE_STATE_MATCH)

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

    def _render_expected_content(self, record: SupervisorRegistryRecord) -> str:
        """详情与漂移判断要区分模板纳管与只读导入快照。"""
        if record.manage_mode == MANAGE_MODE_IMPORTED_READONLY:
            return record.baseline_content

        if any(
            value in (None, "")
            for value in (
                record.job_name,
                record.module_name,
                record.java_path,
                record.active_profile,
                record.port,
                record.jar_name,
                record.xms,
                record.xmx,
                record.run_user,
            )
        ):
            return record.baseline_content

        rendered = self.template_service.render_service(
            job_name=str(record.job_name),
            module_name=str(record.module_name),
            java_path=str(record.java_path),
            active=str(record.active_profile),
            port=int(record.port),
            jar_name=str(record.jar_name),
            config_name=record.config_name,
            xms=str(record.xms),
            xmx=str(record.xmx),
            user=str(record.run_user),
        )
        return rendered.content

    def _detect_file_state(self, host: str, record: SupervisorRegistryRecord, expected_content: str) -> str:
        remote_config = self.config_file_service.read_raw_config_optional_by_config_path(host, record.config_path)
        return self._resolve_file_state(expected_content, remote_config.content if remote_config else None)

    @staticmethod
    def _resolve_file_state(expected_content: str, remote_content: str | None) -> str:
        if remote_content is None:
            return FILE_STATE_MISSING
        if remote_content == expected_content:
            return FILE_STATE_MATCH
        return FILE_STATE_MISMATCH

    def _rollback_remote_create(self, host: str, config_name: str, program_name: str) -> dict[str, object]:
        """落库失败时立即删除刚写入的配置，并执行 reread/update 回滚现场。"""
        rollback: dict[str, object] = {"configRemoved": False, "reread": None, "update": None}
        try:
            self.config_file_service.delete_config(host, config_name, delete_backup=False, program_name=program_name)
            rollback["configRemoved"] = True
        except AppError as exc:
            rollback["configRemoveError"] = exc.msg
            return rollback

        try:
            rollback["reread"] = self.supervisor_service.reread(host)
            rollback["update"] = self.supervisor_service.update(host)
        except AppError as exc:
            rollback["rollbackError"] = exc.msg
        return rollback

    @staticmethod
    def _build_service_payload(
        record: SupervisorRegistryRecord,
        *,
        status: dict[str, object] | None,
        file_state: str,
    ) -> dict[str, object]:
        return {
            "id": record.id,
            "host": record.host_ip,
            "jobName": record.job_name,
            "moduleName": record.module_name,
            "programName": record.program_name,
            "configName": record.config_name,
            "configPath": record.config_path,
            "fileName": record.file_name,
            "contentProgramName": record.content_program_name,
            "manageMode": record.manage_mode,
            "metadataComplete": record.metadata_complete,
            "parseWarnings": list(record.parse_warnings),
            "javaPath": record.java_path,
            "active": record.active_profile,
            "port": record.port,
            "jarName": record.jar_name,
            "xms": record.xms,
            "xmx": record.xmx,
            "user": record.run_user,
            "status": status,
            "fileState": file_state,
        }
