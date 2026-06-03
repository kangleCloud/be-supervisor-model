"""Supervisor 命令封装。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from app.core.exceptions import RemoteCommandError, SupervisorCommandError
from app.core.security import ensure_safe_program_name
from app.services.host_service import HostService


@dataclass(frozen=True)
class SupervisorStatus:
    """Supervisor 状态条目。"""

    program_name: str
    state: str
    raw: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class SupervisorService:
    """集中封装 supervisorctl 操作。"""

    def __init__(self, host_service: HostService):
        self.host_service = host_service

    def status(self, host: str, program_name: Optional[str] = None) -> list[SupervisorStatus]:
        """查询服务状态。"""
        command = ["supervisorctl", "status"]
        if program_name:
            command.append(ensure_safe_program_name(program_name))
        result = self._run(host, command, allow_non_zero=bool(program_name))
        return self._parse_status_output(result.stdout)

    def start(self, host: str, program_name: str) -> dict[str, object]:
        """启动服务。"""
        result = self._run(host, ["supervisorctl", "start", ensure_safe_program_name(program_name)])
        return self._command_result_payload(result)

    def stop(self, host: str, program_name: str, allow_not_running: bool = False) -> dict[str, object]:
        """停止服务。"""
        result = self._run(
            host,
            ["supervisorctl", "stop", ensure_safe_program_name(program_name)],
            allow_non_zero=allow_not_running,
        )
        if not result.success and allow_not_running:
            combined = f"{result.stdout}\n{result.stderr}".lower()
            if "not running" in combined:
                return self._command_result_payload(result)
        if not result.success:
            raise SupervisorCommandError("停止服务失败", self._command_result_payload(result))
        return self._command_result_payload(result)

    def restart(self, host: str, program_name: str) -> dict[str, object]:
        """重启服务。"""
        result = self._run(host, ["supervisorctl", "restart", ensure_safe_program_name(program_name)])
        return self._command_result_payload(result)

    def reread(self, host: str) -> dict[str, object]:
        """执行 reread。"""
        result = self._run(host, ["supervisorctl", "reread"])
        return self._command_result_payload(result)

    def update(self, host: str) -> dict[str, object]:
        """执行 update。"""
        result = self._run(host, ["supervisorctl", "update"])
        return self._command_result_payload(result)

    def _run(self, host: str, command: list[str], allow_non_zero: bool = False):
        executor = self.host_service.get_executor(host)
        result = executor.run_command(command)
        if result.exit_code == 127:
            raise RemoteCommandError("受控命令不可用", self._command_result_payload(result))
        if not result.success and not allow_non_zero:
            raise SupervisorCommandError("Supervisor 命令执行失败", self._command_result_payload(result))
        return result

    @staticmethod
    def _parse_status_output(stdout: str) -> list[SupervisorStatus]:
        statuses: list[SupervisorStatus] = []
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(maxsplit=2)
            if len(parts) >= 2:
                statuses.append(
                    SupervisorStatus(
                        program_name=parts[0],
                        state=parts[1],
                        raw=stripped,
                    )
                )
        return statuses

    @staticmethod
    def _command_result_payload(result) -> dict[str, object]:
        return {
            "args": list(result.args),
            "exitCode": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
