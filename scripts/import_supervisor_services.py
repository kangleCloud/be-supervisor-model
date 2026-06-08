#!/usr/bin/env python3
"""只读扫描远端 Supervisor 配置，并按需导入数据库。"""
from __future__ import annotations

import argparse

from app.core.config import HostConfig, get_settings
from app.core.database import initialize_database
from app.core.exceptions import AppError
from app.services.config_file_service import ConfigFileService, RawConfig
from app.services.host_service import HostService
from app.services.supervisor_registry_service import (
    MANAGE_MODE_IMPORTED_READONLY,
    SupervisorRegistryCreateData,
    SupervisorRegistryService,
)
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

    if args.apply:
        initialize_database(settings)

    imported = 0
    updated = 0
    skipped = 0
    planned = 0
    target_hosts = list(iter_target_hosts(settings.hosts, args.host))
    if not target_hosts:
        print(f"未找到可导入主机: {args.host}")
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"== 导入模式 {mode}，recursive={args.recursive} ==")
    for host in target_hosts:
        print(f"== 处理主机 {host.ip} ({host.name}) ==")
        for raw_config in config_file_service.list_raw_configs(host.ip, include_backups=False, recursive=args.recursive):
            try:
                record = build_registry_data(template_service, host.ip, raw_config)
                if args.apply:
                    _, created = registry_service.upsert_imported(
                        record,
                        operator_id=0,
                        operator_name="system",
                        remark="导入远端 Supervisor 只读快照",
                    )
                    if created:
                        imported += 1
                        print(f"已导入: {host.ip} {record.config_path} -> {record.content_program_name}")
                    else:
                        updated += 1
                        print(f"已更新: {host.ip} {record.config_path} -> {record.content_program_name}")
                    continue

                planned += 1
                print(
                    "计划导入: "
                    f"{host.ip} {record.config_path} -> {record.content_program_name} "
                    f"metadataComplete={record.metadata_complete} warnings={len(record.parse_warnings)}"
                )
            except AppError as exc:
                skipped += 1
                print(f"跳过配置: {host.ip} {raw_config.config_path}，原因: {exc.msg}")
            except Exception as exc:  # noqa: BLE001
                skipped += 1
                print(f"跳过配置: {host.ip} {raw_config.config_path}，原因: {exc}")

    print(
        "导入完成: "
        f"mode={mode}, planned={planned}, imported={imported}, updated={updated}, skipped={skipped}"
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


if __name__ == "__main__":
    raise SystemExit(main())
