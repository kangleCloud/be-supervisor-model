"""Supervisor 业务编排服务。"""
from __future__ import annotations

import logging

from app.core.exceptions import AppError, InternalError
from app.schemas.supervisor import ServiceCreateRequest
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_registry_service import (
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
    ):
        self.host_service = host_service
        self.template_service = template_service
        self.config_file_service = config_file_service
        self.port_check_service = port_check_service
        self.supervisor_service = supervisor_service
        self.registry_service = registry_service

    def list_hosts(self) -> list[dict[str, object]]:
        """返回允许的主机列表。"""
        return self.host_service.list_hosts()

    def list_services(self, host: str) -> list[dict[str, object]]:
        """列出数据库中的纳管服务，并实时补充文件状态与运行状态。"""
        self.host_service.get_host(host)
        status_map = {item.program_name: item.to_dict() for item in self.supervisor_service.status(host)}
        result: list[dict[str, object]] = []
        for record in self.registry_service.list_by_host(host):
            expected_content = self._render_expected_content(record)
            file_state = self._detect_file_state(host, record, expected_content)
            result.append(
                self._build_service_payload(
                    record,
                    status=status_map.get(record.program_name),
                    file_state=file_state,
                )
            )
        return result

    def get_service_detail(self, host: str, program_name: str) -> dict[str, object]:
        """返回纳管服务详情、期望配置与远端漂移信息。"""
        self.host_service.get_host(host)
        record = self.registry_service.get_by_program_name(host, program_name)
        expected_content = self._render_expected_content(record)
        remote_config = self.config_file_service.read_raw_config_optional(host, record.config_name, record.program_name)
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
        self.host_service.get_host(payload.host)
        rendered = self.template_service.render(payload)
        registry_data = SupervisorRegistryCreateData(
            host_ip=payload.host,
            job_name=payload.job_name,
            module_name=payload.module_name,
            program_name=rendered.program_name,
            config_name=rendered.config_name,
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
            LOGGER.exception("create supervisor registry failed", exc_info=exc)
            raise InternalError("新增服务写库失败", rollback_result) from exc

        status_entries = self.supervisor_service.status(payload.host, record.program_name)
        status = status_entries[0].to_dict() if status_entries else None
        return self._build_service_payload(record, status=status, file_state=FILE_STATE_MATCH)

    def _render_expected_content(self, record: SupervisorRegistryRecord) -> str:
        """数据库字段是主数据，详情和漂移判断都以此渲染期望配置。"""
        rendered = self.template_service.render_service(
            job_name=record.job_name,
            module_name=record.module_name,
            java_path=record.java_path,
            active=record.active_profile,
            port=record.port,
            jar_name=record.jar_name,
            config_name=record.config_name,
            xms=record.xms,
            xmx=record.xmx,
            user=record.run_user,
        )
        return rendered.content

    def _detect_file_state(self, host: str, record: SupervisorRegistryRecord, expected_content: str) -> str:
        remote_config = self.config_file_service.read_raw_config_optional(host, record.config_name, record.program_name)
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
