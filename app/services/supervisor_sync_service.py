"""Supervisor 单服务详情同步服务。"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.async_utils import run_blocking
from app.core.exceptions import AppError, ArchivedServiceSyncError, ParamError
from app.schemas.supervisor import ServiceSyncResponse
from app.services.config_file_service import ConfigFileService, RawConfig
from app.services.host_service import HostService
from app.services.supervisor_registry_service import SupervisorRegistryRecord, SupervisorRegistryService
from app.services.supervisor_service import SupervisorService
from app.services.template_service import ParsedConfig, TemplateService


LOGGER = logging.getLogger(__name__)


class SupervisorSyncService:
    """显式同步单服务远端状态和配置快照。"""

    def __init__(
        self,
        host_service: HostService,
        config_file_service: ConfigFileService,
        registry_service: SupervisorRegistryService,
        supervisor_service: SupervisorService,
        template_service: TemplateService,
    ):
        self.host_service = host_service
        self.config_file_service = config_file_service
        self.registry_service = registry_service
        self.supervisor_service = supervisor_service
        self.template_service = template_service

    async def sync_service(self, host: str, program_name: str) -> dict[str, object]:
        """读取远端状态与配置，并把结果回写数据库详情快照。"""
        record = await self._load_record(host, program_name)
        sync_time = datetime.now()
        warnings: list[str] = []
        synced_fields: list[str] = []
        command_results: dict[str, object] = {}
        sync_status = "SUCCESS"
        sync_error: str | None = None

        LOGGER.info("同步服务详情：目标主机=%s，服务名称=%s", host, record.content_program_name)

        status, pid, uptime, status_loaded, status_error = await self._resolve_status(
            host,
            record,
            command_results=command_results,
            warnings=warnings,
        )
        synced_fields.extend(["status", "pid", "uptime", "statusSyncTime"])
        if not status_loaded:
            sync_status = "FAILED"
            sync_error = status_error or f"未读取到服务状态条目: {record.content_program_name}"

        current_config, current_parsed, config_state, config_error = await self._read_current_config(
            host,
            record,
            command_results=command_results,
            warnings=warnings,
        )
        if config_state != "OK":
            sync_status = "FAILED"
            sync_error = config_error or sync_error
        if current_config is not None:
            synced_fields.append("configContent")

        backup_config, backup_state, backup_error = await self._read_backup_config(
            host,
            record,
            command_results=command_results,
            warnings=warnings,
        )
        if backup_state == "ERROR":
            sync_status = "FAILED"
            sync_error = backup_error or sync_error
        synced_fields.extend(["hasBackup", "backupConfigContent"])

        if current_config is not None:
            if current_parsed is None:
                warnings.append("当前配置已同步原文，但结构化字段解析失败，保留数据库原值")
            else:
                synced_fields.extend(
                    [
                        "command",
                        "directory",
                        "stdoutLogfile",
                        "jobName",
                        "moduleName",
                        "javaPath",
                        "active",
                        "port",
                        "jarName",
                        "xms",
                        "xmx",
                        "user",
                    ]
                )

        merged = self._merge_record(record, current_parsed, current_config)
        if config_state == "MISSING":
            merged["config_content"] = None

        await self.registry_service.update_detail_sync_snapshot(
            host,
            record.content_program_name,
            sync_time=sync_time,
            status=status,
            pid=pid,
            uptime=uptime,
            command=merged["command"],
            directory=merged["directory"],
            stdout_logfile=merged["stdout_logfile"],
            job_name=merged["job_name"],
            module_name=merged["module_name"],
            java_path=merged["java_path"],
            active_profile=merged["active_profile"],
            port=merged["port"],
            jar_name=merged["jar_name"],
            xms=merged["xms"],
            xmx=merged["xmx"],
            run_user=merged["run_user"],
            has_backup=record.has_backup if backup_state == "ERROR" else backup_config is not None,
            config_content=merged["config_content"],
            backup_config_content=(
                record.backup_config_content
                if backup_state == "ERROR"
                else (backup_config.content if backup_config is not None else None)
            ),
            sync_status=sync_status,
            sync_error=sync_error,
        )

        LOGGER.info(
            "同步服务详情完成：目标主机=%s，服务名称=%s，同步状态=%s，运行状态=%s",
            host,
            record.content_program_name,
            sync_status,
            status,
        )
        return ServiceSyncResponse(
            host=host,
            contentProgramName=record.content_program_name,
            status=status,
            pid=pid,
            uptime=uptime,
            syncedFields=list(dict.fromkeys(synced_fields)),
            warnings=list(dict.fromkeys(warnings)),
            lastSyncAt=sync_time.strftime("%Y-%m-%d %H:%M:%S"),
            syncStatus=sync_status,
            syncError=sync_error,
            commandResults=command_results,
        ).model_dump(by_alias=True)

    async def _load_record(self, host: str, program_name: str) -> SupervisorRegistryRecord:
        await run_blocking(self.host_service.get_host, host)
        record = await self.registry_service.get_by_content_program_name(host, program_name)
        if record.is_archived:
            LOGGER.warning("服务已归档，禁止同步详情：目标主机=%s，服务名称=%s", host, record.content_program_name)
            raise ArchivedServiceSyncError()
        return record

    async def _resolve_status(
        self,
        host: str,
        record: SupervisorRegistryRecord,
        *,
        command_results: dict[str, object],
        warnings: list[str],
    ) -> tuple[str, str | None, str | None, bool, str | None]:
        try:
            statuses, command_result = await run_blocking(self.supervisor_service.status_with_result, host, record.content_program_name)
        except AppError as exc:
            command_results["status"] = {"ok": False, "error": exc.msg, "data": exc.data}
            warnings.append(f"读取 Supervisor 状态失败: {exc.msg}")
            return record.status, record.pid, record.uptime, False, f"读取 Supervisor 状态失败: {exc.msg}"

        command_results["status"] = {"ok": True, "result": command_result}
        if not statuses:
            warnings.append(f"未读取到服务状态条目: {record.content_program_name}")
            return "UNKNOWN", None, None, False, f"未读取到服务状态条目: {record.content_program_name}"
        entry = statuses[0]
        return entry.state, entry.pid, entry.uptime, True, None

    async def _read_current_config(
        self,
        host: str,
        record: SupervisorRegistryRecord,
        *,
        command_results: dict[str, object],
        warnings: list[str],
    ) -> tuple[RawConfig | None, ParsedConfig | None, str, str | None]:
        try:
            raw_config = await run_blocking(
                self.config_file_service.read_raw_config_optional_by_config_path,
                host,
                record.config_path,
            )
        except AppError as exc:
            command_results["config"] = {
                "configPath": record.config_path,
                "ok": False,
                "error": exc.msg,
                "data": exc.data,
            }
            warnings.append(f"读取当前配置失败: {exc.msg}")
            return None, None, "ERROR", f"读取当前配置失败: {exc.msg}"

        command_results["config"] = {"configPath": record.config_path, "ok": True, "exists": raw_config is not None}
        if raw_config is None:
            warnings.append(f"当前配置文件不存在: {record.config_path}")
            return None, None, "MISSING", f"当前配置文件不存在: {record.config_path}"
        try:
            parsed = await run_blocking(self.template_service.parse, raw_config.content)
        except ParamError as exc:
            warnings.append(exc.msg)
            return raw_config, None, "OK", None
        return raw_config, parsed, "OK", None

    async def _read_backup_config(
        self,
        host: str,
        record: SupervisorRegistryRecord,
        *,
        command_results: dict[str, object],
        warnings: list[str],
    ) -> tuple[RawConfig | None, str, str | None]:
        backup_path = f"{record.config_path}.bak"
        try:
            raw_backup = await run_blocking(
                self.config_file_service.read_raw_config_optional_by_config_path,
                host,
                backup_path,
                allow_backups=True,
            )
        except AppError as exc:
            command_results["backup"] = {
                "configPath": backup_path,
                "ok": False,
                "error": exc.msg,
                "data": exc.data,
            }
            warnings.append(f"读取备份配置失败: {exc.msg}")
            return None, "ERROR", f"读取备份配置失败: {exc.msg}"

        command_results["backup"] = {"configPath": backup_path, "ok": True, "exists": raw_backup is not None}
        return raw_backup, ("OK" if raw_backup is not None else "MISSING"), None

    def _merge_record(
        self,
        record: SupervisorRegistryRecord,
        parsed: ParsedConfig | None,
        current_config: RawConfig | None,
    ) -> dict[str, object]:
        if parsed is None:
            return {
                "command": record.command,
                "directory": record.directory,
                "stdout_logfile": record.stdout_logfile,
                "job_name": record.job_name,
                "module_name": record.module_name,
                "java_path": record.java_path,
                "active_profile": record.active_profile,
                "port": record.port,
                "jar_name": record.jar_name,
                "xms": record.xms,
                "xmx": record.xmx,
                "run_user": record.run_user,
                "config_content": current_config.content if current_config is not None else record.config_content,
            }

        options = parsed.options
        return {
            "command": self._prefer_new_text(options.get("command"), record.command),
            "directory": self._prefer_new_text(options.get("directory"), record.directory),
            "stdout_logfile": self._prefer_new_text(options.get("stdout_logfile"), record.stdout_logfile),
            "job_name": parsed.job_name or record.job_name,
            "module_name": parsed.module_name or record.module_name,
            "java_path": parsed.java_path or record.java_path,
            "active_profile": parsed.active or record.active_profile,
            "port": parsed.port if parsed.port is not None else record.port,
            "jar_name": parsed.jar_name or record.jar_name,
            "xms": parsed.xms or record.xms,
            "xmx": parsed.xmx or record.xmx,
            "run_user": (options.get("user") or parsed.run_user or record.run_user),
            "config_content": current_config.content if current_config is not None else record.config_content,
        }

    @staticmethod
    def _prefer_new_text(value: str | None, fallback: str | None) -> str | None:
        normalized = (value or "").strip()
        if normalized:
            return normalized
        return fallback
