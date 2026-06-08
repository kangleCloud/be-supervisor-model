"""模板服务测试。"""
from __future__ import annotations

from pathlib import Path

from app.schemas.supervisor import ServiceCreateRequest
from app.services.template_service import TemplateService


def test_template_render_and_parse(settings):
    service = TemplateService(settings)
    payload = ServiceCreateRequest(
        host="127.0.0.1",
        jobName="test-agent_be-suda",
        moduleName="sjfy-admin",
        javaPath="/usr/local/jdk17/bin/java",
        active="prod",
        port=9001,
        jarName="sjfy-admin.jar",
        configName="",
        xms="128m",
        xmx="256m",
        user="root",
    )

    rendered = service.render(payload)
    parsed = service.parse(rendered.content)

    assert rendered.program_name == "test-agent_be-suda_sjfy-admin"
    assert rendered.config_name == "test-agent_be-suda_sjfy-admin.ini"
    assert "[program:test-agent_be-suda_sjfy-admin]" in rendered.content
    assert parsed.port == 9001
    assert parsed.job_name == "test-agent_be-suda"
    assert parsed.module_name == "sjfy-admin"
    assert parsed.active == "prod"
    assert parsed.jar_name == "sjfy-admin.jar"
    assert parsed.xms == "128m"
    assert parsed.xmx == "256m"
    assert parsed.run_user == "root"
    assert parsed.metadata_complete is True
    assert parsed.warnings == ()
    assert "autostart=true" in rendered.content
    assert "startsecs=10" in rendered.content
    assert "autorestart=true" in rendered.content
    assert "startretries=3" in rendered.content
    assert "priority=999" in rendered.content
    assert "redirect_stderr=true" in rendered.content
    assert "stdout_logfile_maxbytes=1GB" in rendered.content
    assert "stdout_logfile_backups=1" in rendered.content
    assert "stopasgroup=false" in rendered.content
    assert "killasgroup=false" in rendered.content


def test_config_example_no_longer_exposes_template_defaults(settings):
    config_example = Path(settings.repo_root / "config.example.yaml").read_text(encoding="utf-8")

    assert "templateDefaults" not in config_example


def test_template_parse_accepts_legacy_duplicate_keys_and_space_section(settings):
    service = TemplateService(settings)
    content = (
        "[program: be-entry-exit_sjfy-admin]\n"
        "command=/usr/local/jdk17/bin/java -jar -Xms256m -Xmx512m "
        "-Dspring.profiles.active=prod -Dserver.port=9100 /data/content//be-entry-exit/sjfy-admin.jar\n"
        "directory=/data/content/be-entry-exit/\n"
        "stdout_logfile_maxbytes=50MB\n"
        "stdout_logfile_maxbytes=1GB\n"
    )

    parsed = service.parse(content)

    assert parsed.program_name == "be-entry-exit_sjfy-admin"
    assert parsed.job_name == "be-entry-exit"
    assert parsed.module_name == "sjfy-admin"
    assert parsed.port == 9100
    assert parsed.jar_name == "sjfy-admin.jar"
    assert parsed.options["stdout_logfile_maxbytes"] == "1GB"
    assert parsed.metadata_complete is False
    assert any("重复 key" in warning for warning in parsed.warnings)
