"""Supervisor 初始化导入编排服务。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.core.async_utils import run_blocking
from app.core.exceptions import AppError, ConfigNotFoundError, FileOperationError, ParamError
from app.core.formatting import format_datetime_text
from app.executor.base import ExecutorRuntimeError
from app.services.config_file_service import ConfigFileService, RawConfig
from app.services.host_service import HostService
from app.services.supervisor_registry_service import (
    ImportStagingService,
    MANAGE_MODE_IMPORTED_READONLY,
    MANAGE_MODE_TEMPLATE_MANAGED,
    SupervisorRegistryCreateData,
    SupervisorRegistryRecord,
    SupervisorRegistryService,
)
from app.services.template_service import TemplateService


IMPORT_MODE_PRECHECK = "PRECHECK"
IMPORT_MODE_COMMIT = "COMMIT"
ALLOWED_IMPORT_MODES = {IMPORT_MODE_PRECHECK, IMPORT_MODE_COMMIT}

IMPORT_RESULT_PLANNED = "PLANNED"
IMPORT_RESULT_IMPORTED = "IMPORTED"
IMPORT_RESULT_UPDATED = "UPDATED"
IMPORT_RESULT_SKIPPED = "SKIPPED"

IMPORT_REMARK = "初始化导入 Supervisor 只读快照"
IMPORT_PREFLIGHT_KIND_INVENTORY_MISS = "inventory_miss"
IMPORT_PREFLIGHT_KIND_UNREACHABLE = "unreachable"
IMPORT_PREFLIGHT_KIND_EMPTY_DIR = "empty_dir"
IMPORT_PREFLIGHT_KIND_READ_ERROR = "read_error"
LOGGER = logging.getLogger(__name__)


_IMPORT_UNREACHABLE_MARKERS = (
    "unreachable",
    "failed to connect",
    "connection timed out",
    "connection refused",
    "permission denied",
    "host key verification failed",
    "could not resolve hostname",
    "authentication failed",
    "ansible 命令执行超时",
    "ssh",
)


@dataclass(frozen=True)
class SupervisorImportItem:
    """单个配置文件的导入结果。"""

    config_path: str
    file_name: str
    content_program_name: str | None
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
    batch_id: str
    summary: SupervisorImportSummary
    items: tuple[SupervisorImportItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "mode": self.mode,
            "batchId": self.batch_id,
            "summary": self.summary.to_dict(),
            "items": [item.to_dict() for item in self.items],
        }


def _log_hostname_diagnostic(host: str, executor_type: str, executor) -> None:
    """探测目标主机 hostname 并记录调试日志，不影响主流程。"""
    try:
        result = executor.run_command(["hostname"])
        if result.success:
            LOGGER.debug("[SUPERVISOR_IMPORT_DEBUG] host=%s, executor_type=%s, hostname=%s", host, executor_type, result.stdout.strip())
        else:
            error_text = result.stderr.strip() or result.stdout.strip() or "unknown error"
            LOGGER.warning("[SUPERVISOR_IMPORT_DEBUG] host=%s, executor_type=%s, hostname 探测失败: %s", host, executor_type, error_text)
    except ExecutorRuntimeError as exc:
        LOGGER.warning("[SUPERVISOR_IMPORT_DEBUG] host=%s, executor_type=%s, hostname 探测异常: %s", host, executor_type, exc)


def _log_config_paths_diagnostic(config_paths: list[str]) -> None:
    """输出扫描到的配置路径到日志，便于联调定位。"""
    if not config_paths:
        LOGGER.info("[SUPERVISOR_IMPORT_DEBUG] 未发现任何 *.ini 配置")
        return
    LOGGER.info("[SUPERVISOR_IMPORT_DEBUG] 发现 %s 个配置文件", len(config_paths))
    for path in config_paths:
        LOGGER.debug("[SUPERVISOR_IMPORT_DEBUG] configPath=%s, fileName=%s", path, PurePosixPath(path).name)


def _detect_preflight_failure_kind(message: str) -> str:
    """导入前置扫描只收敛为少数几类诊断，便于 API 稳定映射状态码。"""
    normalized = message.lower()
    if "目标主机未匹配" in message or "could not match supplied host pattern" in normalized or "no hosts matched" in normalized:
        return IMPORT_PREFLIGHT_KIND_INVENTORY_MISS
    if any(marker in normalized for marker in _IMPORT_UNREACHABLE_MARKERS):
        return IMPORT_PREFLIGHT_KIND_UNREACHABLE
    return IMPORT_PREFLIGHT_KIND_READ_ERROR


def _log_preflight_failure_diagnostic(host: str, executor_type: str, kind: str, message: str) -> None:
    """统一输出前置扫描失败分类，便于从服务端日志直接判断失败类型。"""
    LOGGER.warning(
        "[SUPERVISOR_IMPORT_DEBUG] host=%s, executor_type=%s, preflight_failed kind=%s: %s",
        host,
        executor_type,
        kind,
        message,
    )


def _log_finish_and_return(prefix: str, start_time: float, item: SupervisorImportItem) -> SupervisorImportItem:
    """输出单文件 finish 日志并返回 item。"""
    elapsed = time.time() - start_time
    LOGGER.debug("%s finish result=%s elapsed=%.3fs", prefix, item.result, elapsed)
    return item


class SupervisorImportService:
    """统一编排初始化导入的预检与正式写库。"""

    def __init__(
        self,
        host_service: HostService,
        config_file_service: ConfigFileService,
        template_service: TemplateService,
        registry_service: SupervisorRegistryService,
        staging_service: ImportStagingService,
    ):
        self.host_service = host_service
        self.config_file_service = config_file_service
        self.template_service = template_service
        self.registry_service = registry_service
        self.staging_service = staging_service

    async def execute(
        self,
        *,
        host: str,
        mode: str,
        operator_id: int,
        operator_name: str,
        batch_id: str | None = None,
        recursive: bool = True,
    ) -> SupervisorImportReport:
        normalized_mode = self._normalize_mode(mode)
        if normalized_mode == IMPORT_MODE_PRECHECK:
            return await self._execute_precheck(
                host=host,
                operator_id=operator_id,
                operator_name=operator_name,
                recursive=recursive,
            )
        return await self._execute_commit(
            host=host,
            batch_id=(batch_id or "").strip(),
            operator_id=operator_id,
            operator_name=operator_name,
        )

    async def load_staging(
        self,
        *,
        host: str,
        operator_id: int,
    ) -> dict[str, object]:
        """按当前用户与主机恢复最近一次预检批次，供前端刷新后继续展示。"""
        # 这里不主动清空暂存，避免用户刷新页面或重新进入弹窗时丢失上一次 PRECHECK 结果。
        safe_host = (await run_blocking(self.host_service.get_host, host)).ip
        await self.staging_service.delete_expired_batches()
        records = await self.staging_service.get_latest_batch(host_ip=safe_host, operator_id=operator_id)
        if not records:
            return {
                "host": safe_host,
                "exists": False,
                "batchId": None,
                "createdAt": None,
                "summary": {
                    "planned": 0,
                    "imported": 0,
                    "updated": 0,
                    "skipped": 0,
                },
                "items": [],
            }

        batch_id = records[0].batch_id
        report = self.staging_service.build_report_from_records(
            host=safe_host,
            batch_id=batch_id,
            records=records,
        )
        return {
            "host": safe_host,
            "exists": True,
            "batchId": batch_id,
            "createdAt": format_datetime_text(records[0].create_time),
            "summary": report["summary"],
            "items": report["items"],
        }

    async def _execute_precheck(
        self,
        *,
        host: str,
        operator_id: int,
        operator_name: str,
        recursive: bool,
    ) -> SupervisorImportReport:
        """扫描远端配置，写入暂存表并返回预检明细。"""
        # PRECHECK 只做远端只读扫描和暂存写入，不修改正式表，也不清空本次批次结果。
        overall_start = time.time()
        host_config = await run_blocking(self.host_service.get_host, host)
        safe_host = host_config.ip
        executor = await run_blocking(self.host_service.get_executor, safe_host)
        batch_id = self.staging_service.create_batch_id()
        _log_hostname_diagnostic(safe_host, host_config.executor_type, executor)

        await self.staging_service.delete_expired_batches()
        await self.staging_service.clear_operator_host_batches(host_ip=safe_host, operator_id=operator_id)

        config_paths = await self._scan_config_paths(safe_host, host_config.executor_type, recursive)
        total = len(config_paths)
        items: list[SupervisorImportItem] = []
        staging_rows: list[dict[str, object]] = []
        batch_program_paths: dict[str, str] = {}
        for index, config_path in enumerate(config_paths, start=1):
            item, staging_row = await self._precheck_config_path(
                host=safe_host,
                config_path=config_path,
                index=index,
                total=total,
                batch_program_paths=batch_program_paths,
            )
            items.append(item)
            staging_rows.append(staging_row)

        await self.staging_service.insert_batch(
            batch_id=batch_id,
            host_ip=safe_host,
            operator_id=operator_id,
            operator_name=operator_name,
            items=staging_rows,
        )

        result_items = tuple(items)
        summary = SupervisorImportSummary(
            planned=sum(1 for item in result_items if item.result != IMPORT_RESULT_SKIPPED),
            imported=0,
            updated=0,
            skipped=sum(1 for item in result_items if item.result == IMPORT_RESULT_SKIPPED),
        )
        elapsed = time.time() - overall_start
        LOGGER.info(
            "[SUPERVISOR_IMPORT_DEBUG] 导入汇总: host=%s, mode=%s, batchId=%s, total=%s, planned=%s, skipped=%s, elapsed=%.3fs",
            safe_host,
            IMPORT_MODE_PRECHECK,
            batch_id,
            total,
            summary.planned,
            summary.skipped,
            elapsed,
        )
        return SupervisorImportReport(
            host=safe_host,
            mode=IMPORT_MODE_PRECHECK,
            batch_id=batch_id,
            summary=summary,
            items=result_items,
        )

    async def _execute_commit(
        self,
        *,
        host: str,
        batch_id: str,
        operator_id: int,
        operator_name: str,
    ) -> SupervisorImportReport:
        """按 batchId 把预检批次原子提交到正式表。"""
        # COMMIT 必须复用 PRECHECK 已落暂存的 batchId；只有整批提交成功后才会删除暂存。
        if not batch_id:
            raise ParamError("COMMIT 模式必须传 batchId")
        safe_host = (await run_blocking(self.host_service.get_host, host)).ip
        commit_results = await self.staging_service.commit_batch(
            batch_id=batch_id,
            host_ip=safe_host,
            operator_id=operator_id,
            operator_name=operator_name,
            registry_service=self.registry_service,
            remark=IMPORT_REMARK,
        )
        items = tuple(
            self._build_item_from_record(
                record,
                result=IMPORT_RESULT_IMPORTED if created else IMPORT_RESULT_UPDATED,
                message=self._build_commit_message(existing_by_path, created),
            )
            for record, created, existing_by_path in commit_results
        )
        summary = SupervisorImportSummary(
            planned=len(items),
            imported=sum(1 for item in items if item.result == IMPORT_RESULT_IMPORTED),
            updated=sum(1 for item in items if item.result == IMPORT_RESULT_UPDATED),
            skipped=0,
        )
        return SupervisorImportReport(
            host=safe_host,
            mode=IMPORT_MODE_COMMIT,
            batch_id=batch_id,
            summary=summary,
            items=items,
        )

    async def _scan_config_paths(self, host: str, executor_type: str, recursive: bool) -> list[str]:
        """统一扫描目标主机配置目录。"""
        try:
            config_paths = sorted(
                self.config_file_service.to_relative_config_path(path)
                for path in await run_blocking(
                    self.config_file_service.list_config_paths,
                    host,
                    False,
                    recursive=recursive,
                )
            )
        except FileOperationError as exc:
            kind = _detect_preflight_failure_kind(exc.msg)
            _log_preflight_failure_diagnostic(host, executor_type, kind, exc.msg)
            if kind == IMPORT_PREFLIGHT_KIND_INVENTORY_MISS:
                raise ConfigNotFoundError("目标主机未匹配") from exc
            if kind == IMPORT_PREFLIGHT_KIND_UNREACHABLE:
                raise ConfigNotFoundError("目标主机不可达") from exc
            raise

        _log_config_paths_diagnostic(config_paths)
        if not config_paths:
            _log_preflight_failure_diagnostic(
                host,
                executor_type,
                IMPORT_PREFLIGHT_KIND_EMPTY_DIR,
                "远端目录下无可用配置文件",
            )
            raise ConfigNotFoundError("远端目录下无可用配置文件")
        return config_paths

    async def _precheck_config_path(
        self,
        *,
        host: str,
        config_path: str,
        index: int,
        total: int,
        batch_program_paths: dict[str, str],
    ) -> tuple[SupervisorImportItem, dict[str, object]]:
        """单文件预检，返回面向前端的 item 与暂存表写入行。"""
        file_start = time.time()
        file_name = PurePosixPath(config_path).name
        prefix = f"[SUPERVISOR_IMPORT_DEBUG] [{index}/{total}] {config_path}"

        LOGGER.debug("%s start", prefix)

        # === read ===
        try:
            read_start = time.time()
            raw_config = await run_blocking(self.config_file_service.read_raw_config_by_config_path_direct, host, config_path)
            read_elapsed = time.time() - read_start
            LOGGER.debug("%s read_done elapsed=%.3fs", prefix, read_elapsed)
        except ConfigNotFoundError as exc:
            item = self._build_skipped_item(config_path=config_path, file_name=file_name, message=exc.msg)
            return _log_finish_and_return(prefix, file_start, item), self._build_staging_row_from_item(item)
        except FileOperationError as exc:
            item = self._build_skipped_item(config_path=config_path, file_name=file_name, message=exc.msg)
            return _log_finish_and_return(prefix, file_start, item), self._build_staging_row_from_item(item)
        except ExecutorRuntimeError as exc:
            item = self._build_skipped_item(config_path=config_path, file_name=file_name, message=str(exc))
            return _log_finish_and_return(prefix, file_start, item), self._build_staging_row_from_item(item)
        except Exception as exc:  # noqa: BLE001
            item = self._build_skipped_item(config_path=config_path, file_name=file_name, message=self._stringify_unexpected_error(exc))
            return _log_finish_and_return(prefix, file_start, item), self._build_staging_row_from_item(item)

        # === parse ===
        try:
            parse_start = time.time()
            data = await run_blocking(build_import_registry_data, self.template_service, host, raw_config)
            parse_elapsed = time.time() - parse_start
            LOGGER.debug(
                "%s parse_done contentProgramName=%s metadataComplete=%s warnings=%s elapsed=%.3fs",
                prefix,
                data.content_program_name,
                data.metadata_complete,
                len(data.parse_warnings),
                parse_elapsed,
            )
        except AppError as exc:
            item = self._build_skipped_item(config_path=config_path, file_name=file_name, message=exc.msg)
            return _log_finish_and_return(prefix, file_start, item), self._build_staging_row_from_item(item, baseline_content=raw_config.content)
        except Exception as exc:  # noqa: BLE001
            item = self._build_skipped_item(config_path=config_path, file_name=file_name, message=self._stringify_unexpected_error(exc))
            return _log_finish_and_return(prefix, file_start, item), self._build_staging_row_from_item(item, baseline_content=raw_config.content)

        # === plan ===
        try:
            duplicate_path = batch_program_paths.get(data.content_program_name)
            if duplicate_path is not None and duplicate_path != config_path:
                raise ParamError(f"同一批次存在重复 contentProgramName: {data.content_program_name}")
            plan_start = time.time()
            normalized, existing_by_path = await self.registry_service.plan_import_upsert(data)
            plan_elapsed = time.time() - plan_start
            batch_program_paths[normalized.content_program_name] = config_path
            result_so_far = IMPORT_RESULT_PLANNED
            LOGGER.debug("%s plan_done result=%s elapsed=%.3fs", prefix, result_so_far, plan_elapsed)
        except AppError as exc:
            skipped = self._build_item_from_data(data, result=IMPORT_RESULT_SKIPPED, message=exc.msg)
            return _log_finish_and_return(prefix, file_start, skipped), self._build_staging_row_from_data(data, result=IMPORT_RESULT_SKIPPED, message=exc.msg)
        except Exception as exc:  # noqa: BLE001
            skipped = self._build_item_from_data(data, result=IMPORT_RESULT_SKIPPED, message=self._stringify_unexpected_error(exc))
            return _log_finish_and_return(prefix, file_start, skipped), self._build_staging_row_from_data(data, result=IMPORT_RESULT_SKIPPED, message=self._stringify_unexpected_error(exc))

        item = self._build_item_from_data(
            normalized,
            result=IMPORT_RESULT_PLANNED,
            message=self._build_precheck_message(existing_by_path),
        )
        return _log_finish_and_return(prefix, file_start, item), self._build_staging_row_from_data(
            normalized,
            result=IMPORT_RESULT_PLANNED,
            message=item.message,
        )

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = (mode or "").strip().upper()
        if normalized not in ALLOWED_IMPORT_MODES:
            raise ParamError("mode 只支持 PRECHECK 或 COMMIT")
        return normalized

    @staticmethod
    def _build_precheck_message(existing_by_path: SupervisorRegistryRecord | None) -> str:
        if existing_by_path is None:
            return "预检通过，正式导入时将新增记录"
        if existing_by_path.manage_mode == MANAGE_MODE_TEMPLATE_MANAGED:
            return "预检通过，正式导入时将覆盖原模板纳管记录"
        return "预检通过，正式导入时将更新已有导入快照"

    @staticmethod
    def _build_commit_message(existing_by_path: SupervisorRegistryRecord | None, created: bool) -> str:
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

    def _build_staging_row_from_data(
        self,
        data: SupervisorRegistryCreateData,
        *,
        result: str,
        message: str,
    ) -> dict[str, object]:
        return {
            "config_path": data.config_path,
            "file_name": data.file_name,
            "content_program_name": data.content_program_name,
            "baseline_content": data.baseline_content,
            "metadata_complete": data.metadata_complete,
            "parse_warnings": tuple(data.parse_warnings),
            "job_name": data.job_name,
            "module_name": data.module_name,
            "java_path": data.java_path,
            "active_profile": data.active_profile,
            "port": data.port,
            "jar_name": data.jar_name,
            "xms": data.xms,
            "xmx": data.xmx,
            "run_user": data.run_user,
            "result": result,
            "message": message,
        }

    def _build_staging_row_from_item(
        self,
        item: SupervisorImportItem,
        *,
        baseline_content: str | None = None,
    ) -> dict[str, object]:
        return {
            "config_path": item.config_path,
            "file_name": item.file_name,
            "content_program_name": item.content_program_name,
            "baseline_content": baseline_content,
            "metadata_complete": item.metadata_complete,
            "parse_warnings": tuple(item.parse_warnings),
            "job_name": item.job_name,
            "module_name": item.module_name,
            "java_path": item.java_path,
            "active_profile": item.active,
            "port": item.port,
            "jar_name": item.jar_name,
            "xms": item.xms,
            "xmx": item.xmx,
            "run_user": item.user,
            "result": item.result,
            "message": item.message,
        }

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
