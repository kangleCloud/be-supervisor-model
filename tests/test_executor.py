"""执行器测试。"""
from __future__ import annotations

from types import SimpleNamespace

from app.executor.local import LocalExecutor
from app.services.host_service import HostService


def test_local_executor_uses_argument_list(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, capture_output, text, timeout, check=False):  # noqa: ANN001
        captured["command"] = command
        captured["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.executor.local.subprocess.run", fake_run)
    executor = LocalExecutor(30)
    result = executor.run_command(["supervisorctl", "status", "demo;rm"])

    assert result.success
    assert captured["command"] == ["supervisorctl", "status", "demo;rm"]
    assert captured["timeout"] == 30


def test_host_service_selects_ansible_executor(settings):
    service = HostService(settings)

    local_executor = service.get_executor("127.0.0.1")
    remote_executor = service.get_executor("10.1.0.104")

    assert local_executor.__class__.__name__ == "LocalExecutor"
    assert remote_executor.__class__.__name__ == "AnsibleExecutor"
