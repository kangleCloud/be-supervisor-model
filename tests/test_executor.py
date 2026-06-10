"""执行器测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.exceptions import ForbiddenOperationError
from app.executor.ansible import AnsibleExecutor
from app.executor.base import CommandResult, ExecutorRuntimeError
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


def test_ansible_executor_uses_ip_pattern_without_o_and_minimal_callback(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)
    captured: dict[str, object] = {}

    def fake_run(command, capture_output, text, timeout, env, check=False):  # noqa: ANN001
        captured["command"] = command
        captured["timeout"] = timeout
        captured["env"] = env
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.executor.ansible.subprocess.run", fake_run)

    result = executor._run_ansible("shell", "true")

    assert result.success
    assert captured["command"][0] == "ansible"
    assert captured["command"][1] == "10.1.0.104"
    assert "-o" not in captured["command"]
    assert captured["env"]["ANSIBLE_STDOUT_CALLBACK"] == "ansible.builtin.minimal"
    assert captured["timeout"] == settings.executor.ansible_timeout_seconds


def test_host_service_rejects_remote_mutation(settings):
    service = HostService(settings)

    with pytest.raises(ForbiddenOperationError, match="当前项目禁止修改远端配置文件"):
        service.ensure_mutation_allowed("10.1.0.104", "当前项目禁止修改远端配置文件")


def test_supervisor_service_allows_remote_runtime_commands(settings, monkeypatch):
    host_service = HostService(settings)
    service = SupervisorService(host_service)
    captured: list[tuple[str, ...]] = []

    fake_executor = SimpleNamespace(
        run_command=lambda command: (
            captured.append(tuple(command)) or CommandResult(tuple(command), 0, "updated", "")
        )
    )
    monkeypatch.setattr(host_service, "get_executor", lambda host_value: fake_executor)

    result = service.reread("10.1.0.104")

    assert result["exitCode"] == 0
    assert captured == [("supervisorctl", "reread")]


def test_ansible_executor_list_configs_parses_and_sorts_stdout(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    monkeypatch.setattr(
        executor,
        "_run_ansible",
        lambda module, module_args, timeout=None: SimpleNamespace(  # noqa: ARG005
            success=True,
            stdout="10.1.0.104 | CHANGED | rc=0 >>\n/etc/supervisord.d/b.ini\n/etc/supervisord.d/saas/a.ini",
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
            stdout="10.1.0.104 | CHANGED | rc=0 >>\ndemo STOPPED Not started\ndemo2 RUNNING pid 1, uptime 0:00:10",
            stderr="",
            exit_code=0,
            args=("ansible",),
        ),
    )

    result = executor.run_command(["supervisorctl", "status"])

    assert result.stdout == "demo STOPPED Not started\ndemo2 RUNNING pid 1, uptime 0:00:10"


def test_ansible_executor_run_command_keeps_unreachable_payload(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    monkeypatch.setattr(
        executor,
        "_run_ansible",
        lambda module, module_args, timeout=None: SimpleNamespace(  # noqa: ARG005
            success=False,
            stdout=(
                '10.1.0.104 | UNREACHABLE! => {"changed": false, '
                '"msg": "Failed to connect to the host via ssh: Connection timed out"}'
            ),
            stderr="",
            exit_code=4,
            args=("ansible",),
        ),
    )

    result = executor.run_command(["hostname"])

    assert not result.success
    assert "Failed to connect to the host via ssh" in result.stdout
    assert "Connection timed out" in result.stdout


def test_ansible_executor_run_command_extracts_multiline_failed_payload(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    monkeypatch.setattr(
        executor,
        "_run_ansible",
        lambda module, module_args, timeout=None: SimpleNamespace(  # noqa: ARG005
            success=False,
            stdout=(
                "10.1.0.104 | FAILED! => {\n"
                '    "changed": false,\n'
                '    "msg": "Permission denied (publickey,password)."\n'
                "}"
            ),
            stderr="",
            exit_code=2,
            args=("ansible",),
        ),
    )

    result = executor.run_command(["hostname"])

    assert not result.success
    assert result.stdout.startswith("{")
    assert "Permission denied" in result.stdout


def test_ansible_executor_still_compat_with_oneline_stdout_payload(settings, monkeypatch):
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


def test_ansible_executor_list_configs_raises_with_unreachable_payload(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    monkeypatch.setattr(
        executor,
        "_run_ansible",
        lambda module, module_args, timeout=None: SimpleNamespace(  # noqa: ARG005
            success=False,
            stdout=(
                '10.1.0.104 | UNREACHABLE! => {"changed": false, '
                '"msg": "Failed to connect to the host via ssh: Connection timed out"}'
            ),
            stderr="",
            exit_code=4,
            args=(),
        ),
    )

    with pytest.raises(ExecutorRuntimeError, match="Connection timed out"):
        executor.list_configs(settings.supervisor.conf_dir)


def test_ansible_executor_ignores_warning_stderr_on_success(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    def fake_run(command, capture_output, text, timeout, env, check=False):  # noqa: ANN001
        return SimpleNamespace(
            returncode=0,
            stdout="10.1.0.104 | CHANGED | rc=0 >>\n/etc/supervisord.d/a.ini",
            stderr=(
                "[WARNING]: Deprecation warnings can be disabled by setting `deprecation_warnings=False` in ansible.cfg.\n"
                "[DEPRECATION WARNING]: The '-o' argument is deprecated."
            ),
        )

    monkeypatch.setattr("app.executor.ansible.subprocess.run", fake_run)

    paths = executor.list_configs(settings.supervisor.conf_dir, recursive=False, include_backups=False)

    assert [str(path) for path in paths] == ["/etc/supervisord.d/a.ini"]


def test_ansible_executor_raises_with_real_error_after_warning_cleanup(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    def fake_run(command, capture_output, text, timeout, env, check=False):  # noqa: ANN001
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "[WARNING]: Deprecation warnings can be disabled by setting `deprecation_warnings=False` in ansible.cfg.\n"
                "[DEPRECATION WARNING]: The '-o' argument is deprecated.\n"
                "find: '/etc/supervisord.d': No such file or directory"
            ),
        )

    monkeypatch.setattr("app.executor.ansible.subprocess.run", fake_run)

    with pytest.raises(ExecutorRuntimeError, match="No such file or directory"):
        executor.list_configs(settings.supervisor.conf_dir)


def test_ansible_executor_keeps_inventory_mismatch_rewrite_after_warning_cleanup(settings, monkeypatch):
    remote_host = next(host for host in settings.hosts if host.ip == "10.1.0.104")
    executor = AnsibleExecutor(remote_host, settings.executor)

    def fake_run(command, capture_output, text, timeout, env, check=False):  # noqa: ANN001
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "[WARNING]: Collection at '/tmp/example' does not have a MANIFEST.json file.\n"
                "[WARNING]: Could not match supplied host pattern, ignoring: 10.1.0.104"
            ),
        )

    monkeypatch.setattr("app.executor.ansible.subprocess.run", fake_run)

    result = executor._run_ansible("shell", "true")

    assert result.exit_code == 1
    assert result.stderr == "目标主机未匹配: ansible inventory 中未找到 10.1.0.104"
