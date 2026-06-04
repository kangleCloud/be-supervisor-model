#!/usr/bin/env python3
"""一次性导入现有 Supervisor 配置到主数据表。"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.database import initialize_database
from app.core.exceptions import AppError
from app.services.config_file_service import ConfigFileService, RawConfig
from app.services.host_service import HostService
from app.services.supervisor_registry_service import SupervisorRegistryCreateData, SupervisorRegistryService
from app.services.template_service import TemplateService


def main() -> int:
    settings = get_settings()
    initialize_database(settings)

    host_service = HostService(settings)
    template_service = TemplateService(settings)
    config_file_service = ConfigFileService(settings, host_service, template_service)
    registry_service = SupervisorRegistryService(settings)

    imported = 0
    skipped = 0
    for host in settings.hosts:
        if not host.enabled:
            continue
        print(f"== 导入主机 {host.ip} ==")
        for raw_config in config_file_service.list_raw_configs(host.ip, include_backups=False):
            try:
                record = build_registry_data(template_service, host.ip, raw_config)
                registry_service.ensure_can_create(record)
                registry_service.create(
                    record,
                    operator_id=0,
                    operator_name="system",
                    remark="导入现有Supervisor配置",
                )
                imported += 1
                print(f"导入成功: {host.ip} {record.program_name}")
            except AppError as exc:
                skipped += 1
                print(f"跳过配置: {host.ip} {raw_config.config_name}，原因: {exc.msg}")
            except Exception as exc:  # noqa: BLE001
                skipped += 1
                print(f"跳过配置: {host.ip} {raw_config.config_name}，原因: {exc}")

    print(f"导入完成: imported={imported}, skipped={skipped}")
    return 0


def build_registry_data(template_service: TemplateService, host_ip: str, raw_config: RawConfig) -> SupervisorRegistryCreateData:
    """只有能完整反解出受管字段的配置才允许导入。"""
    parsed = template_service.parse(raw_config.content)
    missing_fields = [
        field_name
        for field_name, value in (
            ("jobName", parsed.job_name),
            ("moduleName", parsed.module_name),
            ("port", parsed.port),
            ("javaPath", parsed.java_path),
            ("active", parsed.active),
            ("jarName", parsed.jar_name),
            ("xms", parsed.xms),
            ("xmx", parsed.xmx),
            ("user", parsed.run_user),
        )
        if value in (None, "")
    ]
    if missing_fields:
        joined = ", ".join(missing_fields)
        raise ValueError(f"配置反解析字段不完整: {joined}")

    return SupervisorRegistryCreateData(
        host_ip=host_ip,
        job_name=str(parsed.job_name),
        module_name=str(parsed.module_name),
        program_name=parsed.program_name,
        config_name=raw_config.config_name,
        java_path=str(parsed.java_path),
        active_profile=str(parsed.active),
        port=int(parsed.port),
        jar_name=str(parsed.jar_name),
        xms=str(parsed.xms),
        xmx=str(parsed.xmx),
        run_user=str(parsed.run_user),
    )


if __name__ == "__main__":
    raise SystemExit(main())
