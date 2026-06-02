"""模板服务测试。"""
from __future__ import annotations

from pathlib import Path

from app.schemas.supervisor import ServiceUpsertRequest
from app.services.template_service import TemplateService


def test_template_render_and_parse(settings):
    service = TemplateService(settings)
    payload = ServiceUpsertRequest(
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
    assert parsed.active == "prod"
    assert parsed.jar_name == "sjfy-admin.jar"
    assert parsed.xms == "128m"
    assert parsed.xmx == "256m"
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
