"""测试公共夹具。"""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings


PROGRAM_PATTERN = re.compile(r"\[program:(?P<name>[^\]]+)\]")


def _build_ini(program_name: str, port: int, job_name: str = "demo", module_name: str = "member") -> str:
    return (
        f"[program:{program_name}]\n"
        f"command=/usr/local/jdk17/bin/java -jar -Xms128m -Xmx128m "
        f"-Dspring.profiles.active=prod -Dserver.port={port} "
        f"/data/content/{job_name}/{module_name}/{module_name}.jar\n"
        f"directory=/data/content/{job_name}/{module_name}\n"
        "autostart=true\n"
        "startsecs=10\n"
        "autorestart=true\n"
        "startretries=3\n"
        "user=root\n"
        "priority=999\n"
        "redirect_stderr=true\n"
        "stdout_logfile_maxbytes=1GB\n"
        "stdout_logfile_backups=1\n"
        "stopasgroup=false\n"
        "killasgroup=false\n"
        f"stdout_logfile=/data/logs/{job_name}/{job_name}-{module_name}.log\n"
    )


class FakeSupervisorCtl:
    """模拟 supervisorctl 行为。"""

    def __init__(self, conf_dir: Path):
        self.conf_dir = conf_dir
        self.states: dict[str, str] = {}

    def _current_programs(self) -> list[str]:
        programs: list[str] = []
        for path in sorted(self.conf_dir.glob("*.ini")):
            content = path.read_text(encoding="utf-8")
            match = PROGRAM_PATTERN.search(content)
            if match:
                programs.append(match.group("name"))
        return programs

    def run(self, command, capture_output, text, timeout, check=False):  # noqa: ANN001
        args = list(command)
        if not args or args[0] != "supervisorctl":
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        action = args[1]
        current_programs = self._current_programs()
        for name in current_programs:
            self.states.setdefault(name, "STOPPED")

        if action == "status":
            if len(args) == 2:
                lines = []
                for name in current_programs:
                    state = self.states.get(name, "STOPPED")
                    detail = "pid 1, uptime 0:00:10" if state == "RUNNING" else "Not started"
                    lines.append(f"{name} {state} {detail}")
                return SimpleNamespace(returncode=0, stdout="\n".join(lines), stderr="")

            name = args[2]
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            state = self.states.get(name, "STOPPED")
            detail = "pid 1, uptime 0:00:10" if state == "RUNNING" else "Not started"
            return SimpleNamespace(returncode=0, stdout=f"{name} {state} {detail}", stderr="")

        if len(args) < 3 and action in {"start", "stop", "restart"}:
            return SimpleNamespace(returncode=2, stdout="", stderr="missing program name")

        name = args[2] if len(args) >= 3 else ""
        if action == "start":
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            self.states[name] = "RUNNING"
            return SimpleNamespace(returncode=0, stdout=f"{name}: started", stderr="")
        if action == "stop":
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            if self.states.get(name) != "RUNNING":
                return SimpleNamespace(returncode=7, stdout="", stderr=f"{name}: ERROR (not running)")
            self.states[name] = "STOPPED"
            return SimpleNamespace(returncode=0, stdout=f"{name}: stopped", stderr="")
        if action == "restart":
            if name not in current_programs:
                return SimpleNamespace(returncode=3, stdout="", stderr="ERROR (no such process)")
            self.states[name] = "RUNNING"
            return SimpleNamespace(returncode=0, stdout=f"{name}: restarted", stderr="")
        if action == "reread":
            return SimpleNamespace(returncode=0, stdout="No config updates to processes", stderr="")
        if action == "update":
            return SimpleNamespace(returncode=0, stdout="updated", stderr="")

        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture()
def test_environment(tmp_path, monkeypatch):
    """准备测试配置。"""
    conf_dir = tmp_path / "supervisord.d"
    conf_dir.mkdir()
    inventory_path = tmp_path / "inventory.ini"
    inventory_path.write_text("[web]\n10.1.0.104\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "app:",
                "  host: 127.0.0.1",
                "  port: 18880",
                "  logLevel: info",
                "supervisor:",
                f"  confDir: {conf_dir}",
                "  commandTimeoutSeconds: 30",
                "executor:",
                "  type: local",
                f"  inventoryPath: {inventory_path}",
                "  remoteUser: root",
                "  timeoutSeconds: 30",
                "hosts:",
                "  - name: local",
                "    ip: 127.0.0.1",
                "    enabled: true",
                "    executorType: local",
                "  - name: web-104",
                "    ip: 10.1.0.104",
                "    enabled: true",
                "    executorType: ansible",
                "    ansiblePattern: web-104",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("APP_CONFIG_PATH", str(config_path))
    get_settings.cache_clear()
    yield {"conf_dir": conf_dir, "config_path": config_path, "build_ini": _build_ini}
    get_settings.cache_clear()


@pytest.fixture()
def fake_supervisor(test_environment, monkeypatch):
    """模拟本地 supervisorctl。"""
    controller = FakeSupervisorCtl(test_environment["conf_dir"])
    local_module = importlib.import_module("app.executor.local")
    monkeypatch.setattr(local_module.subprocess, "run", controller.run)
    return controller


@pytest.fixture()
def settings(test_environment):
    """返回测试配置对象。"""
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def client(test_environment, fake_supervisor):
    """创建测试客户端。"""
    get_settings.cache_clear()
    main_module = importlib.import_module("app.main")
    app = main_module.create_app()
    return TestClient(app)
