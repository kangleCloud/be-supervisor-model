"""导入脚本测试。"""
from __future__ import annotations

import importlib

from app.services.config_file_service import RawConfig


def test_build_registry_data_keeps_file_name_and_content_program_name_redundant(settings):
    module = importlib.import_module("scripts.import_supervisor_services")
    template_service = importlib.import_module("app.services.template_service").TemplateService(settings)
    raw_config = RawConfig(
        config_path="saas/legacy-name.ini",
        file_name="legacy-name.ini",
        config_name="legacy-name.ini",
        path="/etc/supervisord.d/saas/legacy-name.ini",
        content=(
            "[program:legacy_sjfy-admin]\n"
            "command=/usr/local/jdk17/bin/java -jar -Xms256m -Xmx512m "
            "-Dspring.profiles.active=prod -Dserver.port=9200 /data/content//legacy/sjfy-admin.jar\n"
            "directory=/data/content/legacy/\n"
            "stdout_logfile_maxbytes=50MB\n"
            "stdout_logfile_maxbytes=1GB\n"
        ),
    )

    record = module.build_registry_data(template_service, "10.1.0.104", raw_config)

    assert record.host_ip == "10.1.0.104"
    assert record.config_path == "saas/legacy-name.ini"
    assert record.file_name == "legacy-name.ini"
    assert record.content_program_name == "legacy_sjfy-admin"
    assert record.metadata_complete is False
    assert any("重复 key" in warning for warning in record.parse_warnings)


def test_import_script_dry_run_does_not_write_database(test_environment, fake_mysql, monkeypatch):
    module = importlib.import_module("scripts.import_supervisor_services")
    conf_dir = test_environment["conf_dir"]
    (conf_dir / "demo_member.ini").write_text(test_environment["build_ini"]("demo_member", 9001), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["import_supervisor_services.py", "--host", "127.0.0.1"])

    exit_code = module.main()

    assert exit_code == 0
    assert fake_mysql.tables.get("sys_supervisor_service") in (None, [])


def test_import_script_apply_upserts_database(test_environment, fake_mysql, monkeypatch):
    module = importlib.import_module("scripts.import_supervisor_services")
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    (sub_dir / "legacy-name.ini").write_text(
        (
            "[program:legacy_sjfy-admin]\n"
            "command=/usr/local/jdk17/bin/java -jar -Xms256m -Xmx512m "
            "-Dspring.profiles.active=prod -Dserver.port=9200 /data/content//legacy/sjfy-admin.jar\n"
            "directory=/data/content/legacy/\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["import_supervisor_services.py", "--host", "127.0.0.1", "--recursive", "--apply"],
    )

    exit_code = module.main()

    assert exit_code == 0
    assert len(fake_mysql.tables["sys_supervisor_service"]) == 1
    assert fake_mysql.tables["sys_supervisor_service"][0]["config_path"] == "saas/legacy-name.ini"
    assert fake_mysql.tables["sys_supervisor_service"][0]["file_name"] == "legacy-name.ini"
