"""配置文件服务测试。"""
from __future__ import annotations

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
    restore_result = service.restore_config("127.0.0.1", "demo_member.ini", "demo_member")

    assert first_backup["backupPath"].endswith("demo_member.ini.bak")
    assert second_backup["archivedBackupPath"] is not None
    assert (conf_dir / "demo_member.ini.bak").exists()
    assert restore_result["archivedCurrentPath"] is not None
    assert "9002" not in config_path.read_text(encoding="utf-8")
