#!/usr/bin/env python3
"""只读扫描远端 Supervisor 配置，并按需导入数据库。"""
from __future__ import annotations

import argparse

from app.core.config import HostConfig, get_settings
from app.core.exceptions import AppError
from app.core.database import initialize_database
from app.services.config_file_service import ConfigFileService, RawConfig
from app.services.host_service import HostService
from app.services.supervisor_import_service import (
    IMPORT_MODE_APPLY,
    IMPORT_MODE_DRY_RUN,
    IMPORT_RESULT_IMPORTED,
    IMPORT_RESULT_PLANNED,
    IMPORT_RESULT_SKIPPED,
    IMPORT_RESULT_UPDATED,
    SupervisorImportReport,
    SupervisorImportService,
    build_import_registry_data,
)
from app.services.supervisor_registry_service import SupervisorRegistryService
from app.services.template_service import TemplateService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入远端 Supervisor 只读快照")
    parser.add_argument("--host", dest="host", default="", help="仅处理指定主机 IP")
    parser.add_argument("--recursive", action="store_true", help="递归扫描 /etc/supervisord.d 子目录")
    parser.add_argument("--apply", action="store_true", help="写入数据库；未传时仅 dry-run")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    host_service = HostService(settings)
    template_service = TemplateService(settings)
    config_file_service = ConfigFileService(settings, host_service, template_service)
    registry_service = SupervisorRegistryService(settings)
    import_service = SupervisorImportService(host_service, config_file_service, template_service, registry_service)

    if args.apply:
        initialize_database(settings)

    target_hosts = list(iter_target_hosts(settings.hosts, args.host))
    if not target_hosts:
        print(f"未找到可导入主机: {args.host}")
        return 1

    summary = {"planned": 0, "imported": 0, "updated": 0, "skipped": 0}
    mode = IMPORT_MODE_APPLY if args.apply else IMPORT_MODE_DRY_RUN
    print(f"== 导入模式 {mode}，recursive={args.recursive} ==")
    for host in target_hosts:
        print(f"== 处理主机 {host.ip} ({host.name}) ==")
        try:
            report = import_service.execute(
                host=host.ip,
                mode=mode,
                operator_id=0,
                operator_name="system",
                recursive=args.recursive,
            )
        except AppError as exc:
            print(f"导入失败: {exc}")
            continue
        _print_report(report)
        summary["planned"] += report.summary.planned
        summary["imported"] += report.summary.imported
        summary["updated"] += report.summary.updated
        summary["skipped"] += report.summary.skipped

    print(
        "导入完成: "
        "mode="
        f"{mode}, planned={summary['planned']}, imported={summary['imported']}, "
        f"updated={summary['updated']}, skipped={summary['skipped']}"
    )
    return 0


def iter_target_hosts(hosts: list[HostConfig], host_filter: str) -> list[HostConfig]:
    """仅返回启用的目标主机，避免脚本触达无效配置。"""
    result: list[HostConfig] = []
    for host in hosts:
        if not host.enabled:
            continue
        if host_filter and host.ip != host_filter:
            continue
        result.append(host)
    return result


def build_registry_data(
    template_service: TemplateService,
    host_ip: str,
    raw_config: RawConfig,
):
    """兼容测试与脚本调用，实际逻辑复用共享导入服务。"""
    return build_import_registry_data(template_service, host_ip, raw_config)


def _print_report(report: SupervisorImportReport) -> None:
    """把结构化结果转换成脚本可读日志，保持与旧输出习惯接近。"""
    for item in report.items:
        if item.result == IMPORT_RESULT_PLANNED:
            print(
                "计划导入: "
                f"{report.host} {item.config_path} -> {item.content_program_name} "
                f"metadataComplete={item.metadata_complete} warnings={len(item.parse_warnings)}"
            )
            continue
        if item.result == IMPORT_RESULT_IMPORTED:
            print(f"已导入: {report.host} {item.config_path} -> {item.content_program_name}")
            continue
        if item.result == IMPORT_RESULT_UPDATED:
            print(f"已更新: {report.host} {item.config_path} -> {item.content_program_name}")
            continue
        if item.result == IMPORT_RESULT_SKIPPED:
            print(f"跳过配置: {report.host} {item.config_path}，原因: {item.message}")


if __name__ == "__main__":
    raise SystemExit(main())
