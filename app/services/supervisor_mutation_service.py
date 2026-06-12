"""Supervisor 增改删编排服务。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.core.async_utils import run_blocking
from app.core.exceptions import AppError, ArchiveStateError, ConfigAlreadyExistsError, InternalError
from app.schemas.supervisor import (
    ServiceCreateRequest,
    ServiceDeleteResponse,
    ServiceUpdateRequest,
    ServiceUpdateResponse,
)
from app.services.auth_service import AuthenticatedUser
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_registry_service import (
    MANAGE_MODE_TEMPLATE_MANAGED,
    SupervisorRegistryCreateData,
    SupervisorRegistryRecord,
    SupervisorRegistryService,
)
from app.services.supervisor_service import SupervisorService
from app.services.template_service import RenderedConfig, TemplateService


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RenderedTarget:
    """渲染后的目标配置定位信息。"""

    rendered: RenderedConfig
    config_path: str
    file_name: str


class SupervisorMutationService:
    """统一处理本地与远端的创建、修改、删除。"""

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

    async def create_service(self, payload: ServiceCreateRequest, current_user: AuthenticatedUser) -> dict[str, object]:
        """创建 Supervisor 服务，远端和本地都允许。"""
        await run_blocking(self.host_service.get_host, payload.host)
        target = self._render_target(payload)
        registry_data = self._build_registry_data(
            host=payload.host,
            target=target,
            job_name=payload.job_name,
            module_name=payload.module_name,
            java_path=payload.java_path,
            active=payload.active,
            port=payload.port,
            jar_name=payload.jar_name or self.template_service.build_default_jar_name(payload.module_name),
            xms=payload.xms,
            xmx=payload.xmx,
            user=payload.user,
            manage_mode=MANAGE_MODE_TEMPLATE_MANAGED,
            baseline_content=target.rendered.content,
            metadata_complete=True,
            parse_warnings=(),
        )
        await self.registry_service.ensure_can_create(registry_data)
        await run_blocking(
            self.config_file_service.ensure_not_exists,
            payload.host,
            target.file_name,
            target.rendered.program_name,
        )
        await run_blocking(self.port_check_service.ensure_no_conflict, payload.host, payload.port)

        LOGGER.info("新增服务：目标主机=%s，服务名称=%s，配置路径=%s", payload.host, target.rendered.program_name, target.config_path)
        await run_blocking(
            self.config_file_service.write_config,
            payload.host,
            target.file_name,
            target.rendered.content,
            target.rendered.program_name,
        )
        reread_result = await run_blocking(self.supervisor_service.reread, payload.host)
        update_result = await run_blocking(self.supervisor_service.update, payload.host)

        try:
            record = await self.registry_service.create(
                registry_data,
                operator_id=current_user.user_id,
                operator_name=current_user.username,
                remark="Supervisor 服务配置",
            )
        except Exception as exc:
            rollback_result = await self._rollback_create(payload.host, target.config_path)
            if isinstance(exc, AppError):
                raise
            LOGGER.exception("新增服务写库失败：目标主机=%s，configPath=%s", payload.host, target.config_path, exc_info=exc)
            raise InternalError("新增服务写库失败", rollback_result) from exc

        return {
            "id": record.id,
            "host": record.host_ip,
            "jobName": record.job_name,
            "moduleName": record.module_name,
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
            "status": None,
            "fileState": "MATCH",
            "commandResults": {
                "reread": reread_result,
                "update": update_result,
            },
            "isArchived": record.is_archived,
            "archivedAt": None,
            "restoredAt": None,
        }

    async def update_service(
        self,
        host: str,
        previous_program_name: str,
        payload: ServiceUpdateRequest,
        current_user: AuthenticatedUser,
    ) -> dict[str, object]:
        """修改 Supervisor 服务，支持改名和改端口。"""
        await run_blocking(self.host_service.get_host, host)
        current_record = await self.registry_service.get_by_content_program_name(host, previous_program_name)
        self._ensure_not_archived(current_record, "服务已归档，不能修改")
        current_content = await run_blocking(
            self.config_file_service.read_raw_config_by_config_path,
            host,
            current_record.config_path,
        )
        target = self._render_target(payload, base_config_path=current_record.config_path)
        registry_data = self._build_registry_data(
            host=host,
            target=target,
            job_name=payload.job_name,
            module_name=payload.module_name,
            java_path=payload.java_path,
            active=payload.active,
            port=payload.port,
            jar_name=payload.jar_name or self.template_service.build_default_jar_name(payload.module_name),
            xms=payload.xms,
            xmx=payload.xmx,
            user=payload.user,
            manage_mode=MANAGE_MODE_TEMPLATE_MANAGED,
            baseline_content=target.rendered.content,
            metadata_complete=True,
            parse_warnings=(),
        )
        await self.registry_service.ensure_can_save(registry_data, exclude_record_id=current_record.id)
        await run_blocking(self._ensure_target_path_available, host, current_record, target.config_path)
        await run_blocking(self._ensure_port_available, host, current_record, payload.port)

        identity_changed = (
            target.rendered.program_name != current_record.content_program_name
            or target.config_path != current_record.config_path
        )
        LOGGER.info(
            "修改服务：目标主机=%s，原服务名称=%s，新服务名称=%s，原配置路径=%s，新配置路径=%s",
            host,
            current_record.content_program_name,
            target.rendered.program_name,
            current_record.config_path,
            target.config_path,
        )

        command_results: dict[str, object] = {}
        if identity_changed:
            command_results["stop"] = await run_blocking(
                self.supervisor_service.stop,
                host,
                current_record.content_program_name,
                True,
            )
        backup_result = await run_blocking(self.config_file_service.backup_config_by_config_path, host, current_record.config_path)
        command_results["backup"] = backup_result
        write_result = await run_blocking(self.config_file_service.write_config_by_config_path, host, target.config_path, target.rendered.content)
        command_results["write"] = write_result
        if target.config_path != current_record.config_path:
            command_results["deleteOld"] = await run_blocking(
                self.config_file_service.delete_config_by_config_path,
                host,
                current_record.config_path,
            )
        reread_result = await run_blocking(self.supervisor_service.reread, host)
        update_result = await run_blocking(self.supervisor_service.update, host)
        command_results["reread"] = reread_result
        command_results["update"] = update_result
        status, pid, uptime = await self._query_runtime_snapshot(host, target.rendered.program_name)
        parsed_target = await run_blocking(self.template_service.parse, target.rendered.content)

        try:
            await self.registry_service.update_service(
                record_id=current_record.id,
                data=registry_data,
                operator_id=current_user.user_id,
                operator_name=current_user.username,
                remark="Supervisor 服务配置",
                status=status,
                pid=pid,
                uptime=uptime,
                command=parsed_target.options.get("command"),
                directory=parsed_target.options.get("directory"),
                stdout_logfile=parsed_target.options.get("stdout_logfile"),
                has_backup=True,
                config_content=target.rendered.content,
                backup_config_content=current_content.content,
                sync_status="SUCCESS",
                sync_error=None,
            )
        except Exception as exc:
            rollback_result = await self._rollback_update(
                host=host,
                previous_config_path=current_record.config_path,
                target_config_path=target.config_path,
            )
            if isinstance(exc, AppError):
                raise
            LOGGER.exception(
                "修改服务写库失败：目标主机=%s，原服务名称=%s，新服务名称=%s",
                host,
                current_record.content_program_name,
                target.rendered.program_name,
                exc_info=exc,
            )
            raise InternalError("修改服务写库失败", rollback_result) from exc

        updated_record = await self.registry_service.get_by_content_program_name(host, target.rendered.program_name)
        return ServiceUpdateResponse(
            host=host,
            previousContentProgramName=current_record.content_program_name,
            contentProgramName=updated_record.content_program_name,
            fileName=updated_record.file_name,
            configPath=updated_record.config_path,
            manageMode=updated_record.manage_mode,
            commandResults=command_results,
        ).model_dump(by_alias=True)

    async def delete_service(self, host: str, program_name: str, current_user: AuthenticatedUser) -> dict[str, object]:
        """删除 Supervisor 服务，必须先停止再删现场和数据库。"""
        del current_user
        await run_blocking(self.host_service.get_host, host)
        record = await self.registry_service.get_by_content_program_name(host, program_name)
        self._ensure_not_archived(record, "服务已归档，不能删除")

        LOGGER.info("删除服务：目标主机=%s，服务名称=%s，配置路径=%s", host, record.content_program_name, record.config_path)
        command_results: dict[str, object] = {
            "stop": await run_blocking(self.supervisor_service.stop, host, record.content_program_name, True),
        }

        existed = await run_blocking(self.config_file_service.exists_by_config_path, host, record.config_path)
        backup_path: str | None = None
        if existed:
            backup_result = await run_blocking(self.config_file_service.backup_config_by_config_path, host, record.config_path)
            command_results["backup"] = backup_result
            command_results["delete"] = await run_blocking(self.config_file_service.delete_config_by_config_path, host, record.config_path)
            backup_path = backup_result["backupPath"]
        else:
            command_results["backup"] = None
            command_results["delete"] = None
            backup_candidate = f"{record.config_path}.bak"
            if await run_blocking(self.config_file_service.exists_by_config_path, host, backup_candidate, allow_backups=True):
                backup_path = backup_candidate

        command_results["reread"] = await run_blocking(self.supervisor_service.reread, host)
        command_results["update"] = await run_blocking(self.supervisor_service.update, host)

        try:
            await self.registry_service.delete_service(record.id)
        except Exception as exc:
            rollback_result = await self._rollback_delete(host, record.config_path, existed)
            if isinstance(exc, AppError):
                raise
            LOGGER.exception("删除服务写库失败：目标主机=%s，服务名称=%s", host, record.content_program_name, exc_info=exc)
            raise InternalError("删除服务写库失败", rollback_result) from exc

        return ServiceDeleteResponse(
            host=host,
            contentProgramName=record.content_program_name,
            deletedConfigPath=record.config_path,
            backupPath=backup_path,
            commandResults=command_results,
        ).model_dump(by_alias=True)

    def _render_target(
        self,
        payload: ServiceCreateRequest | ServiceUpdateRequest,
        *,
        base_config_path: str | None = None,
    ) -> _RenderedTarget:
        rendered = self.template_service.render_service(
            job_name=payload.job_name,
            module_name=payload.module_name,
            content_program_name=payload.content_program_name,
            java_path=payload.java_path,
            active=payload.active,
            port=payload.port,
            jar_name=payload.jar_name,
            config_name=payload.file_name,
            xms=payload.xms,
            xmx=payload.xmx,
            user=payload.user,
        )
        if not base_config_path:
            config_path = rendered.config_name
        else:
            parent = PurePosixPath(base_config_path).parent
            config_path = rendered.config_name if str(parent) == "." else f"{parent.as_posix()}/{rendered.config_name}"
        return _RenderedTarget(rendered=rendered, config_path=config_path, file_name=rendered.config_name)

    @staticmethod
    def _build_registry_data(
        *,
        host: str,
        target: _RenderedTarget,
        job_name: str,
        module_name: str,
        java_path: str,
        active: str,
        port: int,
        jar_name: str,
        xms: str,
        xmx: str,
        user: str,
        manage_mode: str,
        baseline_content: str,
        metadata_complete: bool,
        parse_warnings: tuple[str, ...],
    ) -> SupervisorRegistryCreateData:
        return SupervisorRegistryCreateData(
            host_ip=host,
            job_name=job_name,
            module_name=module_name,
            config_path=target.config_path,
            file_name=target.file_name,
            content_program_name=target.rendered.program_name,
            manage_mode=manage_mode,
            baseline_content=baseline_content,
            metadata_complete=metadata_complete,
            parse_warnings=parse_warnings,
            java_path=java_path,
            active_profile=active,
            port=port,
            jar_name=jar_name,
            xms=xms,
            xmx=xmx,
            run_user=user,
        )

    def _ensure_target_path_available(
        self,
        host: str,
        current_record: SupervisorRegistryRecord,
        target_config_path: str,
    ) -> None:
        if target_config_path == current_record.config_path:
            return
        if self.config_file_service.exists_by_config_path(host, target_config_path):
            raise ConfigAlreadyExistsError(f"配置文件已存在: {target_config_path}")

    def _ensure_port_available(self, host: str, current_record: SupervisorRegistryRecord, target_port: int) -> None:
        check_runtime = current_record.port != target_port
        self.port_check_service.ensure_no_conflict(
            host,
            target_port,
            exclude_config_path=current_record.config_path,
            include_runtime=check_runtime,
        )

    @staticmethod
    def _ensure_not_archived(record: SupervisorRegistryRecord, message: str) -> None:
        if record.is_archived:
            raise ArchiveStateError(message)

    async def _query_runtime_snapshot(self, host: str, program_name: str) -> tuple[str, str | None, str | None]:
        try:
            status_entries = await run_blocking(self.supervisor_service.status, host, program_name)
        except AppError:
            status_entries = []
        entry = status_entries[0] if status_entries else None
        if entry is None:
            return "UNKNOWN", None, None
        return entry.state, entry.pid, entry.uptime

    async def _rollback_create(self, host: str, config_path: str) -> dict[str, object]:
        rollback: dict[str, object] = {"configRemoved": False, "reread": None, "update": None}
        if await run_blocking(self.config_file_service.exists_by_config_path, host, config_path):
            try:
                await run_blocking(self.config_file_service.delete_config_by_config_path, host, config_path)
                rollback["configRemoved"] = True
            except AppError as exc:
                rollback["configRemoveError"] = exc.msg
                return rollback

        try:
            rollback["reread"] = await run_blocking(self.supervisor_service.reread, host)
            rollback["update"] = await run_blocking(self.supervisor_service.update, host)
        except AppError as exc:
            rollback["rollbackError"] = exc.msg
        return rollback

    async def _rollback_update(
        self,
        *,
        host: str,
        previous_config_path: str,
        target_config_path: str,
    ) -> dict[str, object]:
        rollback: dict[str, object] = {"targetRemoved": False, "restored": None, "reread": None, "update": None}
        if target_config_path != previous_config_path and await run_blocking(
            self.config_file_service.exists_by_config_path,
            host,
            target_config_path,
        ):
            try:
                await run_blocking(self.config_file_service.delete_config_by_config_path, host, target_config_path)
                rollback["targetRemoved"] = True
            except AppError as exc:
                rollback["targetRemoveError"] = exc.msg
                return rollback

        try:
            rollback["restored"] = await run_blocking(
                self.config_file_service.restore_config_by_config_path,
                host,
                previous_config_path,
            )
        except AppError as exc:
            rollback["restoreError"] = exc.msg
            return rollback

        try:
            rollback["reread"] = await run_blocking(self.supervisor_service.reread, host)
            rollback["update"] = await run_blocking(self.supervisor_service.update, host)
        except AppError as exc:
            rollback["rollbackError"] = exc.msg
        return rollback

    async def _rollback_delete(self, host: str, config_path: str, existed: bool) -> dict[str, object]:
        rollback: dict[str, object] = {"restored": None, "reread": None, "update": None}
        if existed:
            try:
                rollback["restored"] = await run_blocking(self.config_file_service.restore_config_by_config_path, host, config_path)
            except AppError as exc:
                rollback["restoreError"] = exc.msg
                return rollback

        try:
            rollback["reread"] = await run_blocking(self.supervisor_service.reread, host)
            rollback["update"] = await run_blocking(self.supervisor_service.update, host)
        except AppError as exc:
            rollback["rollbackError"] = exc.msg
        return rollback
