"""Supervisor 初始化导入编排服务。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.core.exceptions import AppError, ConfigNotFoundError, FileOperationError, ParamError
from app.executor.base import ExecutorRuntimeError
from app.services.config_file_service import ConfigFileService, RawConfig
from app.services.host_service import HostService
from app.services.supervisor_registry_service import (
    MANAGE_MODE_IMPORTED_READONLY,
    MANAGE_MODE_TEMPLATE_MANAGED,
    SupervisorRegistryCreateData,
    SupervisorRegistryRecord,
    SupervisorRegistryService,
)
from app.services.template_service import TemplateService


IMPORT_MODE_DRY_RUN = "DRY_RUN"
IMPORT_MODE_APPLY = "APPLY"
ALLOWED_IMPORT_MODES = {IMPORT_MODE_DRY_RUN, IMPORT_MODE_APPLY}

IMPORT_RESULT_PLANNED = "PLANNED"
IMPORT_RESULT_IMPORTED = "IMPORTED"
IMPORT_RESULT_UPDATED = "UPDATED"
IMPORT_RESULT_SKIPPED = "SKIPPED"

IMPORT_REMARK = "初始化导入 Supervisor 只读快照"


@dataclass(frozen=True)
class SupervisorImportItem:
    """单个配置文件的导入结果。"""

    config_path: str
    file_name: str
    content_program_name: str | None
    program_name: str | None
    config_name: str | None
    job_name: str | None
    module_name: str | None
    java_path: str | None
    active: str | None
    port: int | None
    jar_name: str | None
    xms: str | None
    xmx: str | None
    user: str | None
    manage_mode: str | None
    metadata_complete: bool
    parse_warnings: tuple[str, ...]
    result: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "configPath": self.config_path,
            "fileName": self.file_name,
            "contentProgramName": self.content_program_name,
            "programName": self.program_name,
            "configName": self.config_name,
            "jobName": self.job_name,
            "moduleName": self.module_name,
            "javaPath": self.java_path,
            "active": self.active,
            "port": self.port,
            "jarName": self.jar_name,
            "xms": self.xms,
            "xmx": self.xmx,
            "user": self.user,
            "manageMode": self.manage_mode,
            "metadataComplete": self.metadata_complete,
            "parseWarnings": list(self.parse_warnings),
            "result": self.result,
            "message": self.message,
        }


@dataclass(frozen=True)
class SupervisorImportSummary:
    """导入结果汇总。"""

    planned: int
    imported: int
    updated: int
    skipped: int

    def to_dict(self) -> dict[str, int]:
        return {
            "planned": self.planned,
            "imported": self.imported,
            "updated": self.updated,
            "skipped": self.skipped,
        }


@dataclass(frozen=True)
class SupervisorImportReport:
    """面向 API 与脚本复用的结构化导入结果。"""

    host: str
    mode: str
    summary: SupervisorImportSummary
    items: tuple[SupervisorImportItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "mode": self.mode,
            "summary": self.summary.to_dict(),
            "items": [item.to_dict() for item in self.items],
        }


def _print_hostname_diagnostic(host: str, executor_type: str, executor) -> None:
    """探测目标主机 hostname 并输出到控制台，不影响主流程。"""
    try:
        result = executor.run_command(["hostname"])
        if result.success:
            print(f"[SUPERVISOR_IMPORT_DEBUG] host={host}, executor_type={executor_type}, hostname={result.stdout.strip()}")
        else:
            print(f"[SUPERVISOR_IMPORT_DEBUG] host={host}, executor_type={executor_type}, hostname 探测失败: {result.stderr.strip() or 'unknown error'}")
    except ExecutorRuntimeError as exc:
        print(f"[SUPERVISOR_IMPORT_DEBUG] host={host}, executor_type={executor_type}, hostname 探测异常: {exc}")


def _print_config_paths_diagnostic(config_paths: list[str]) -> None:
    """输出扫描到的配置路径到控制台，便于联调定位。"""
    if not config_paths:
        print("[SUPERVISOR_IMPORT_DEBUG] 未发现任何 *.ini 配置")
        return
    print(f"[SUPERVISOR_IMPORT_DEBUG] 发现 {len(config_paths)} 个配置文件:")
    for path in config_paths:
        print(f"  configPath={path}, fileName={PurePosixPath(path).name}")


def _diagnostic_finish_and_return(prefix: str, start_time: float, item: SupervisorImportItem) -> SupervisorImportItem:
    """输出单文件 finish 日志并返回 item。"""
    elapsed = time.time() - start_time
    print(f"{prefix} finish result={item.result} elapsed={elapsed:.3f}s")
    return item


class SupervisorImportService:
    """统一编排初始化导入的预检与正式写库。"""

    def __init__(
        self,
        host_service: HostService,
        config_file_service: ConfigFileService,
        template_service: TemplateService,
        registry_service: SupervisorRegistryService,
    ):
        self.host_service = host_service
        self.config_file_service = config_file_service
        self.template_service = template_service
        self.registry_service = registry_service

    def execute(
        self,
        *,
        host: str,
        mode: str,
        operator_id: int,
        operator_name: str,
        recursive: bool = True,
    ) -> SupervisorImportReport:
        """执行单主机初始化导入，返回逐文件结果。"""
        overall_start = time.time()
        normalized_mode = self._normalize_mode(mode)
        safe_host = self.host_service.get_host(host).ip
        executor = self.host_service.get_executor(safe_host)
        _print_hostname_diagnostic(safe_host, self.host_service.get_host(safe_host).executor_type, executor)

        config_paths = sorted(
            self.config_file_service.to_relative_config_path(path)
            for path in self.config_file_service.list_config_paths(
                safe_host,
                include_backups=False,
                recursive=recursive,
            )
        )
        _print_config_paths_diagnostic(config_paths)
        if not config_paths:
            print("[SUPERVISOR_IMPORT_DEBUG] 未发现任何 *.ini 配置，返回失败")
            raise ConfigNotFoundError("远端目录下无可用配置文件")

        total = len(config_paths)
        items: list[SupervisorImportItem] = []
        for index, config_path in enumerate(config_paths, start=1):
            item = self._process_config_path_with_diagnostics(
                host=safe_host,
                config_path=config_path,
                mode=normalized_mode,
                operator_id=operator_id,
                operator_name=operator_name,
                index=index,
                total=total,
            )
            items.append(item)

        result_items = tuple(items)
        summary = SupervisorImportSummary(
            planned=sum(1 for item in result_items if item.result != IMPORT_RESULT_SKIPPED),
            imported=sum(1 for item in result_items if item.result == IMPORT_RESULT_IMPORTED),
            updated=sum(1 for item in result_items if item.result == IMPORT_RESULT_UPDATED),
            skipped=sum(1 for item in result_items if item.result == IMPORT_RESULT_SKIPPED),
        )
        elapsed = time.time() - overall_start
        print(
            f"[SUPERVISOR_IMPORT_DEBUG] 导入汇总: "
            f"host={safe_host}, mode={normalized_mode}, "
            f"total={total}, planned={summary.planned}, "
            f"imported={summary.imported}, updated={summary.updated}, "
            f"skipped={summary.skipped}, elapsed={elapsed:.3f}s"
        )
        return SupervisorImportReport(host=safe_host, mode=normalized_mode, summary=summary, items=result_items)

    def _process_config_path_with_diagnostics(
        self,
        *,
        host: str,
        config_path: str,
        mode: str,
        operator_id: int,
        operator_name: str,
        index: int,
        total: int,
    ) -> SupervisorImportItem:
        """单文件处理，带逐阶段诊断与耗时输出。"""
        file_start = time.time()
        file_name = PurePosixPath(config_path).name
        prefix = f"[SUPERVISOR_IMPORT_DEBUG] [{index}/{total}] {config_path}"

        print(f"{prefix} start")

        # === read ===
        try:
            read_start = time.time()
            raw_config = self.config_file_service.read_raw_config_by_config_path_direct(host, config_path)
            read_elapsed = time.time() - read_start
            print(f"{prefix} read_done elapsed={read_elapsed:.3f}s")
        except ConfigNotFoundError as exc:
            return _diagnostic_finish_and_return(prefix, file_start, self._build_skipped_item(config_path=config_path, file_name=file_name, message=exc.msg))
        except FileOperationError as exc:
            return _diagnostic_finish_and_return(prefix, file_start, self._build_skipped_item(config_path=config_path, file_name=file_name, message=exc.msg))
        except ExecutorRuntimeError as exc:
            return _diagnostic_finish_and_return(prefix, file_start, self._build_skipped_item(config_path=config_path, file_name=file_name, message=str(exc)))
        except Exception as exc:  # noqa: BLE001
            return _diagnostic_finish_and_return(prefix, file_start, self._build_skipped_item(config_path=config_path, file_name=file_name, message=self._stringify_unexpected_error(exc)))

        # === parse ===
        try:
            parse_start = time.time()
            data = build_import_registry_data(self.template_service, host, raw_config)
            parse_elapsed = time.time() - parse_start
            print(f"{prefix} parse_done programName={data.content_program_name} metadataComplete={data.metadata_complete} warnings={len(data.parse_warnings)} elapsed={parse_elapsed:.3f}s")
        except AppError as exc:
            return _diagnostic_finish_and_return(prefix, file_start, self._build_skipped_item(config_path=config_path, file_name=file_name, message=exc.msg))
        except Exception as exc:  # noqa: BLE001
            return _diagnostic_finish_and_return(prefix, file_start, self._build_skipped_item(config_path=config_path, file_name=file_name, message=self._stringify_unexpected_error(exc)))

        # === plan ===
        try:
            plan_start = time.time()
            normalized, existing_by_path = self.registry_service.plan_import_upsert(data)
            plan_elapsed = time.time() - plan_start
            result_so_far = IMPORT_RESULT_PLANNED if mode == IMPORT_MODE_DRY_RUN else IMPORT_RESULT_IMPORTED if existing_by_path is None else IMPORT_RESULT_UPDATED
            print(f"{prefix} plan_done result={result_so_far} elapsed={plan_elapsed:.3f}s")
        except AppError as exc:
            skipped = self._build_item_from_data(data, result=IMPORT_RESULT_SKIPPED, message=exc.msg)
            return _diagnostic_finish_and_return(prefix, file_start, skipped)
        except Exception as exc:  # noqa: BLE001
            skipped = self._build_item_from_data(data, result=IMPORT_RESULT_SKIPPED, message=self._stringify_unexpected_error(exc))
            return _diagnostic_finish_and_return(prefix, file_start, skipped)

        # === dry-run early return ===
        if mode == IMPORT_MODE_DRY_RUN:
            item = self._build_item_from_data(normalized, result=IMPORT_RESULT_PLANNED, message=self._build_dry_run_message(existing_by_path))
            return _diagnostic_finish_and_return(prefix, file_start, item)

        # === apply ===
        try:
            apply_start = time.time()
            record, created = self.registry_service.upsert_imported(normalized, operator_id=operator_id, operator_name=operator_name, remark=IMPORT_REMARK)
            apply_elapsed = time.time() - apply_start
            result_str = IMPORT_RESULT_IMPORTED if created else IMPORT_RESULT_UPDATED
            print(f"{prefix} apply_done result={result_str} elapsed={apply_elapsed:.3f}s")
        except AppError as exc:
            skipped = self._build_item_from_data(normalized, result=IMPORT_RESULT_SKIPPED, message=exc.msg)
            return _diagnostic_finish_and_return(prefix, file_start, skipped)
        except Exception as exc:  # noqa: BLE001
            skipped = self._build_item_from_data(normalized, result=IMPORT_RESULT_SKIPPED, message=self._stringify_unexpected_error(exc))
            return _diagnostic_finish_and_return(prefix, file_start, skipped)

        item = self._build_item_from_record(record, result=IMPORT_RESULT_IMPORTED if created else IMPORT_RESULT_UPDATED, message=self._build_apply_message(existing_by_path, created))
        return _diagnostic_finish_and_return(prefix, file_start, item)

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = (mode or "").strip().upper()
        if normalized not in ALLOWED_IMPORT_MODES:
            raise ParamError("mode 只支持 DRY_RUN 或 APPLY")
        return normalized

    @staticmethod
    def _build_dry_run_message(existing_by_path: SupervisorRegistryRecord | None) -> str:
        if existing_by_path is None:
            return "预检通过，正式导入时将新增记录"
        if existing_by_path.manage_mode == MANAGE_MODE_TEMPLATE_MANAGED:
            return "预检通过，正式导入时将覆盖原模板纳管记录"
        return "预检通过，正式导入时将更新已有导入快照"

    @staticmethod
    def _build_apply_message(existing_by_path: SupervisorRegistryRecord | None, created: bool) -> str:
        if created or existing_by_path is None:
            return "导入成功"
        if existing_by_path.manage_mode == MANAGE_MODE_TEMPLATE_MANAGED:
            return "已覆盖原模板纳管记录"
        return "已更新已有导入快照"

    @staticmethod
    def _stringify_unexpected_error(exc: Exception) -> str:
        message = str(exc).strip()
        return message or exc.__class__.__name__

    def _build_item_from_data(
        self,
        data: SupervisorRegistryCreateData,
        *,
        result: str,
        message: str,
    ) -> SupervisorImportItem:
        return SupervisorImportItem(
            config_path=data.config_path,
            file_name=data.file_name,
            content_program_name=data.content_program_name,
            program_name=data.program_name,
            config_name=data.config_name,
            job_name=data.job_name,
            module_name=data.module_name,
            java_path=data.java_path,
            active=data.active_profile,
            port=data.port,
            jar_name=data.jar_name,
            xms=data.xms,
            xmx=data.xmx,
            user=data.run_user,
            manage_mode=data.manage_mode,
            metadata_complete=bool(data.metadata_complete),
            parse_warnings=tuple(data.parse_warnings),
            result=result,
            message=message,
        )

    @staticmethod
    def _build_item_from_record(
        record: SupervisorRegistryRecord,
        *,
        result: str,
        message: str,
    ) -> SupervisorImportItem:
        return SupervisorImportItem(
            config_path=record.config_path,
            file_name=record.file_name,
            content_program_name=record.content_program_name,
            program_name=record.program_name,
            config_name=record.config_name,
            job_name=record.job_name,
            module_name=record.module_name,
            java_path=record.java_path,
            active=record.active_profile,
            port=record.port,
            jar_name=record.jar_name,
            xms=record.xms,
            xmx=record.xmx,
            user=record.run_user,
            manage_mode=record.manage_mode,
            metadata_complete=record.metadata_complete,
            parse_warnings=record.parse_warnings,
            result=result,
            message=message,
        )

    @staticmethod
    def _build_skipped_item(*, config_path: str, file_name: str, message: str) -> SupervisorImportItem:
        return SupervisorImportItem(
            config_path=config_path,
            file_name=file_name,
            content_program_name=None,
            program_name=None,
            config_name=file_name,
            job_name=None,
            module_name=None,
            java_path=None,
            active=None,
            port=None,
            jar_name=None,
            xms=None,
            xmx=None,
            user=None,
            manage_mode=MANAGE_MODE_IMPORTED_READONLY,
            metadata_complete=False,
            parse_warnings=(),
            result=IMPORT_RESULT_SKIPPED,
            message=message,
        )


def build_import_registry_data(
    template_service: TemplateService,
    host_ip: str,
    raw_config: RawConfig,
) -> SupervisorRegistryCreateData:
    """尽力把 legacy 配置转成只读快照，结构化字段缺失不阻断导入。"""
    parsed = template_service.parse(raw_config.content)
    return SupervisorRegistryCreateData(
        host_ip=host_ip,
        job_name=parsed.job_name,
        module_name=parsed.module_name,
        program_name=parsed.program_name,
        config_name=raw_config.config_name,
        config_path=raw_config.config_path,
        file_name=raw_config.file_name,
        content_program_name=parsed.program_name,
        manage_mode=MANAGE_MODE_IMPORTED_READONLY,
        baseline_content=raw_config.content,
        metadata_complete=parsed.metadata_complete,
        parse_warnings=parsed.warnings,
        java_path=parsed.java_path,
        active_profile=parsed.active,
        port=parsed.port,
        jar_name=parsed.jar_name,
        xms=parsed.xms,
        xmx=parsed.xmx,
        run_user=parsed.run_user,
    )
