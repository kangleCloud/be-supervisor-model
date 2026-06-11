"""Supervisor 命令封装。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.core.exceptions import RemoteCommandError, SupervisorCommandError
from app.core.security import ensure_safe_program_name
from app.services.host_service import HostService


PID_UPTIME_PATTERN = re.compile(r"pid\s+(?P<pid>\d+),\s+uptime\s+(?P<uptime>\S+)")


ALLOWED_STATUS_VALUES = frozenset({
    "RUNNING", "STOPPED", "FATAL", "BACKOFF", "STARTING", "STOPPING", "EXITED", "UNKNOWN",
})


@dataclass(frozen=True)
class SupervisorStatus:
    """Supervisor 状态条目。"""

    program_name: str
    state: str
    pid: Optional[str]
    uptime: Optional[str]
    raw: str

    def to_dict(self) -> dict[str, object]:
        return {
            "programName": self.program_name,
            "state": self.state,
            "pid": self.pid,
            "uptime": self.uptime,
            "raw": self.raw,
        }


class SupervisorService:
    """集中封装 supervisorctl 操作。"""

    def __init__(self, host_service: HostService):
        self.host_service = host_service

    def _ensure_remote_command_allowed(self, host: str) -> None:
        """运行操作允许 local/ansible，共同前提只有主机必须在白名单内。"""
        self.host_service.get_host(host)

    def status(self, host: str, program_name: Optional[str] = None) -> list[SupervisorStatus]:
        """查询服务状态。"""
        statuses, _ = self.status_with_result(host, program_name)
        return statuses

    def status_with_result(
        self,
        host: str,
        program_name: Optional[str] = None,
    ) -> tuple[list[SupervisorStatus], dict[str, object]]:
        """查询服务状态，并返回原始命令执行结果供同步接口记录。"""
        command = ["supervisorctl", "status"]
        if program_name:
            command.append(ensure_safe_program_name(program_name))
        result = self._run(host, command, allow_non_zero=bool(program_name))
        return self._parse_status_output(result.stdout), self._command_result_payload(result)

    def start(self, host: str, program_name: str) -> dict[str, object]:
        """启动服务。"""
        self._ensure_remote_command_allowed(host)
        result = self._run(host, ["supervisorctl", "start", ensure_safe_program_name(program_name)])
        return self._command_result_payload(result)

    def stop(self, host: str, program_name: str, allow_not_running: bool = False) -> dict[str, object]:
        """停止服务。"""
        self._ensure_remote_command_allowed(host)
        result = self._run(
            host,
            ["supervisorctl", "stop", ensure_safe_program_name(program_name)],
            allow_non_zero=allow_not_running,
        )
        if not result.success and allow_not_running:
            combined = f"{result.stdout}\n{result.stderr}".lower()
            if "not running" in combined or "no such process" in combined:
                return self._command_result_payload(result)
        if not result.success:
            raise SupervisorCommandError("停止服务失败", self._command_result_payload(result))
        return self._command_result_payload(result)

    def restart(self, host: str, program_name: str) -> dict[str, object]:
        """重启服务。"""
        self._ensure_remote_command_allowed(host)
        result = self._run(host, ["supervisorctl", "restart", ensure_safe_program_name(program_name)])
        return self._command_result_payload(result)

    def reread(self, host: str) -> dict[str, object]:
        """执行 reread。"""
        self._ensure_remote_command_allowed(host)
        result = self._run(host, ["supervisorctl", "reread"])
        return self._command_result_payload(result)

    def update(self, host: str) -> dict[str, object]:
        """执行 update。"""
        self._ensure_remote_command_allowed(host)
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
                state = parts[1]
                detail = parts[2] if len(parts) >= 3 else ""
                pid: str | None = None
                uptime: str | None = None
                pid_match = PID_UPTIME_PATTERN.search(detail)
                if pid_match is not None:
                    pid = pid_match.group("pid")
                    uptime = pid_match.group("uptime")
                statuses.append(
                    SupervisorStatus(
                        program_name=parts[0],
                        state=state,
                        pid=pid,
                        uptime=uptime,
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
