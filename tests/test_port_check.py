"""端口冲突检测测试。"""
from __future__ import annotations

from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.port_check_service import PortCheckService
from app.services.template_service import TemplateService


def test_port_check_scans_backups_and_excludes_current(settings, test_environment):
    conf_dir = test_environment["conf_dir"]
    build_ini = test_environment["build_ini"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()

    (conf_dir / "demo_member.ini").write_text(build_ini("demo_member", 9001), encoding="utf-8")
    (conf_dir / "demo_member.ini.bak").write_text(build_ini("demo_member", 9001), encoding="utf-8")
    (sub_dir / "other_member.ini.bak.20260602-153000").write_text(build_ini("other_member", 9001), encoding="utf-8")

    host_service = HostService(settings)
    template_service = TemplateService(settings)
    config_service = ConfigFileService(settings, host_service, template_service)
    port_service = PortCheckService(config_service, host_service)

    conflicts = port_service.find_conflicts("127.0.0.1", 9001, include_runtime=False)
    excluded = port_service.find_conflicts(
        "127.0.0.1",
        9001,
        exclude_config_path="demo_member.ini",
        include_runtime=False,
    )

    assert len(conflicts) == 3
    assert len(excluded) == 1
    assert excluded[0].program_name == "other_member"


def test_port_check_detects_runtime_listeners(settings, test_environment, fake_supervisor):
    host_service = HostService(settings)
    template_service = TemplateService(settings)
    config_service = ConfigFileService(settings, host_service, template_service)
    port_service = PortCheckService(config_service, host_service)

    fake_supervisor.extra_listeners[9909] = "external-java"

    conflicts = port_service.find_conflicts("127.0.0.1", 9909)

    assert len(conflicts) == 1
    assert conflicts[0].kind == "LISTEN"
    assert conflicts[0].source == "ss -lnutp"
