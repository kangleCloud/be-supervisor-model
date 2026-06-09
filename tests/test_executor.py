"""执行器测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.exceptions import ForbiddenOperationError
from app.executor.ansible import AnsibleExecutor
from app.executor.base import ExecutorRuntimeError
from app.executor.local import LocalExecutor
from app.services.host_service import HostService
from app.services.supervisor_service import SupervisorService


def test_local_executor_uses_argument_list(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, capture_output, text, timeout, check=False):  # noqa: ANN001
        captured["command"] = command
        captured["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.executor.local.subprocess.run", fake_run)
    executor = LocalExecutor(300)
    result = executor.run_command(["supervisorctl", "status", "demo;rm"])

    assert result.success
    assert captured["command"] == ["supervisorctl", "status", "demo;rm"]
    assert captured["timeout"] == 300


def test_host_service_selects_ansible_executor(settings):
    service = HostService(settings)

    local_executor = service.get_executor("127.0.0.1")
    remote_executor = service.get_executor("10.1.0.104")

    assert local_executor.__class__.__name__ == "LocalExecutor"
    assert remote_executor.__class__.__name__ == "AnsibleExecutor"


def test_host_service_rejects_remote_mutation(settings):
    service = HostService(settings)

    with pytest.raises(ForbiddenOperationError, match="当前项目禁止修改远端配置文件"):
        service.ensure_mutation_allowed("10.1.0.104", "当前项目禁止修改远端配置文件")


def test_supervisor_service_rejects_remote_mutation(settings):
    service = SupervisorService(HostService(settings))

    with pytest.raises(ForbiddenOperationError, match="当前项目禁止操作远端 Supervisor"):
        service.reread("10.1.0.104")


def test_ansible_executor_list_configs_parses_and_sorts_stdout(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    monkeypatch.setattr(
        executor,
        "_run_ansible",
        lambda module, module_args, timeout=None: SimpleNamespace(  # noqa: ARG005
            success=True,
            stdout="10.1.0.104 | CHANGED | rc=0 | (stdout) /etc/supervisord.d/b.ini\\n/etc/supervisord.d/saas/a.ini",
            stderr="",
            exit_code=0,
            args=(),
        ),
    )

    paths = executor.list_configs(settings.supervisor.conf_dir, recursive=True, include_backups=False)

    assert [str(path) for path in paths] == ["/etc/supervisord.d/b.ini", "/etc/supervisord.d/saas/a.ini"]


def test_ansible_executor_run_command_extracts_shell_stdout(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    monkeypatch.setattr(
        executor,
        "_run_ansible",
        lambda module, module_args, timeout=None: SimpleNamespace(  # noqa: ARG005
            success=True,
            stdout="10.1.0.104 | CHANGED | rc=0 | (stdout) demo STOPPED Not started\\ndemo2 RUNNING pid 1, uptime 0:00:10",
            stderr="",
            exit_code=0,
            args=("ansible",),
        ),
    )

    result = executor.run_command(["supervisorctl", "status"])

    assert result.stdout == "demo STOPPED Not started\ndemo2 RUNNING pid 1, uptime 0:00:10"


def test_ansible_executor_list_configs_raises_when_remote_dir_missing(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    monkeypatch.setattr(
        executor,
        "_run_ansible",
        lambda module, module_args, timeout=None: SimpleNamespace(  # noqa: ARG005
            success=False,
            stdout="",
            stderr="find: '/etc/supervisord.d': No such file or directory",
            exit_code=1,
            args=(),
        ),
    )

    with pytest.raises(ExecutorRuntimeError, match="No such file or directory"):
        executor.list_configs(settings.supervisor.conf_dir)
