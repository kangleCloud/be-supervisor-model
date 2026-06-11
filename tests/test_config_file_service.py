"""配置文件服务测试。"""
from __future__ import annotations

from app.executor.local import LocalExecutor
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.template_service import TemplateService


def test_backup_restore_and_archive_names(settings, test_environment):
    conf_dir = test_environment["conf_dir"]
    build_ini = test_environment["build_ini"]

    config_path = conf_dir / "demo_member.ini"
    config_path.write_text(build_ini("demo_member", 9001), encoding="utf-8")

    host_service = HostService(settings)
    template_service = TemplateService(settings)
    service = ConfigFileService(settings, host_service, template_service)

    first_backup = service.backup_config("127.0.0.1", "demo_member.ini", "demo_member")
    config_path.write_text(build_ini("demo_member", 9002), encoding="utf-8")
    second_backup = service.backup_config("127.0.0.1", "demo_member.ini", "demo_member")
    config_path.write_text(build_ini("demo_member", 9003), encoding="utf-8")
    restore_result = service.restore_config("127.0.0.1", "demo_member.ini", "demo_member")

    assert first_backup["backupPath"].endswith("demo_member.ini.bak")
    assert second_backup["archivedBackupPath"] is not None
    assert (conf_dir / "demo_member.ini.bak").exists()
    assert restore_result["archivedCurrentPath"] is not None
    restored_content = config_path.read_text(encoding="utf-8")
    assert "9003" not in restored_content
    assert "9002" in restored_content


def test_write_config_allows_remote_host(settings, test_environment, monkeypatch):
    host_service = HostService(settings)
    template_service = TemplateService(settings)
    service = ConfigFileService(settings, host_service, template_service)
    conf_dir = test_environment["conf_dir"]

    local_executor = LocalExecutor(settings.supervisor.command_timeout_seconds)
    monkeypatch.setattr(host_service, "get_executor", lambda host_value: local_executor)
    service.write_config("10.1.0.104", "demo_member.ini", "[program:demo_member]\ncommand=/bin/true\n")

    assert conf_dir.joinpath("demo_member.ini").exists()


def test_list_and_read_configs_support_recursive_relative_path(settings, test_environment):
    conf_dir = test_environment["conf_dir"]
    build_ini = test_environment["build_ini"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    (sub_dir / "demo_member.ini").write_text(build_ini("demo_member", 9001), encoding="utf-8")

    host_service = HostService(settings)
    template_service = TemplateService(settings)
    service = ConfigFileService(settings, host_service, template_service)

    raw_configs = service.list_raw_configs("127.0.0.1", include_backups=False, recursive=True)
    loaded = service.read_raw_config_by_config_path("127.0.0.1", "saas/demo_member.ini")

    assert len(raw_configs) == 1
    assert raw_configs[0].config_path == "saas/demo_member.ini"
    assert raw_configs[0].file_name == "demo_member.ini"
    assert loaded.config_path == "saas/demo_member.ini"
