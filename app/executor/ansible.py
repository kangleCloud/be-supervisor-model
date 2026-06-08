"""Ansible 执行器。"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Sequence

from app.core.config import ExecutorSettings, HostConfig
from app.executor.base import CommandResult, ExecutorRuntimeError, RemoteExecutor


_ANSIBLE_ENV = {
    **os.environ,
    "ANSIBLE_FORCE_COLOR": "0",
    "ANSIBLE_HOST_KEY_CHECKING": "False",
}
_RESULT_LINE_PATTERN = re.compile(r"^(?P<host>\S+)\s+\|\s+(?P<status>SUCCESS|CHANGED|FAILED!?|UNREACHABLE!)(?P<rest>.*)$")


class AnsibleExecutor(RemoteExecutor):
    """通过 ansible ad-hoc 命令执行受控操作。"""

    def __init__(self, host: HostConfig, settings: ExecutorSettings):
        super().__init__(settings.ansible_timeout_seconds)
        self.host = host
        self.settings = settings

    @property
    def _pattern(self) -> str:
        return self.host.ansible_pattern or self.host.ip

    def _base_args(self) -> list[str]:
        return [
            "ansible",
            self._pattern,
            "-i",
            str(self.settings.ansible_inventory_path),
            "-u",
            self.settings.ansible_remote_user,
            "-o",
        ]

    def _run_ansible(self, module: str, module_args: str, timeout: int | None = None) -> CommandResult:
        command = self._base_args() + ["-m", module, "-a", module_args]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout_seconds,
                env=_ANSIBLE_ENV,
                check=False,
            )
        except FileNotFoundError as exc:
            return CommandResult(tuple(command), 127, "", str(exc))
        except subprocess.TimeoutExpired as exc:
            return CommandResult(tuple(command), 124, exc.stdout or "", exc.stderr or "ansible 命令执行超时")

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        return CommandResult(tuple(command), proc.returncode, stdout, stderr)

    @staticmethod
    def _split_payload_lines(payload: str) -> list[str]:
        """兼容 oneline 回调把多行 stdout 折叠成 \n 文本的场景。"""
        normalized = payload.replace("\\n", "\n").replace("\\r", "\r")
        return [line.strip() for line in normalized.splitlines() if line.strip()]

    def _extract_stdout_lines(self, stdout: str) -> list[str]:
        """提取 ansible oneline 输出中的真实 stdout 负载，避免把主机前缀误当成业务内容。"""
        payload_lines: list[str] = []
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            match = _RESULT_LINE_PATTERN.match(line)
            if match is None:
                payload_lines.extend(self._split_payload_lines(line))
                continue

            rest = match.group("rest").strip()
            if rest.startswith(">>"):
                payload_lines.extend(self._split_payload_lines(rest[2:].strip()))
                continue
            if "(stdout)" in rest:
                payload_lines.extend(self._split_payload_lines(rest.split("(stdout)", 1)[1].strip()))
                continue

        return payload_lines

    def run_command(self, command: Sequence[str], timeout: int | None = None) -> CommandResult:
        result = self._run_ansible("shell", shlex.join(list(command)), timeout=timeout)
        return CommandResult(
            result.args,
            result.exit_code,
            "\n".join(self._extract_stdout_lines(result.stdout)),
            result.stderr,
        )

    def list_configs(self, conf_dir: Path, *, recursive: bool = False, include_backups: bool = True) -> list[Path]:
        quoted_dir = shlex.quote(str(conf_dir))
        name_parts = ["-name '*.ini'"]
        if include_backups:
            name_parts.extend(["-o -name '*.ini.bak'", "-o -name '*.ini.bak.*'"])
        maxdepth_part = "" if recursive else "-maxdepth 1 "
        shell_command = f"find {quoted_dir} {maxdepth_part}-type f \\( {' '.join(name_parts)} \\) -print"
        result = self._run_ansible("shell", shell_command)
        if not result.success:
            raise ExecutorRuntimeError(result.stderr or result.stdout or "列出配置文件失败")
        return [Path(line) for line in sorted(self._extract_stdout_lines(result.stdout))]

    def read_text(self, path: Path) -> str:
        result = self._run_ansible("shell", f"cat {shlex.quote(str(path))}")
        if not result.success:
            raise ExecutorRuntimeError(result.stderr or result.stdout or "读取远程文件失败")
        return "\n".join(self._extract_stdout_lines(result.stdout))

    def write_text_atomic(self, path: Path, content: str) -> None:
        remote_temp = f"{path}.tmp-{uuid.uuid4().hex}"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            fh.write(content)
            local_temp = fh.name
        try:
            copy_result = self._run_ansible(
                "copy",
                f"src={shlex.quote(local_temp)} dest={shlex.quote(remote_temp)} mode=0644",
            )
            if not copy_result.success:
                raise ExecutorRuntimeError(copy_result.stderr or copy_result.stdout or "远程复制文件失败")
            move_result = self._run_ansible(
                "shell",
                f"mv {shlex.quote(remote_temp)} {shlex.quote(str(path))}",
            )
            if not move_result.success:
                raise ExecutorRuntimeError(move_result.stderr or move_result.stdout or "远程原子写入失败")
        finally:
            Path(local_temp).unlink(missing_ok=True)

    def copy_file(self, source: Path, target: Path) -> None:
        result = self._run_ansible("shell", f"cp {shlex.quote(str(source))} {shlex.quote(str(target))}")
        if not result.success:
            raise ExecutorRuntimeError(result.stderr or result.stdout or "远程复制文件失败")

    def move_file(self, source: Path, target: Path) -> None:
        result = self._run_ansible("shell", f"mv {shlex.quote(str(source))} {shlex.quote(str(target))}")
        if not result.success:
            raise ExecutorRuntimeError(result.stderr or result.stdout or "远程移动文件失败")

    def remove_file(self, path: Path, missing_ok: bool = False) -> None:
        flag = "-f " if missing_ok else ""
        result = self._run_ansible("shell", f"rm {flag}{shlex.quote(str(path))}")
        if not result.success:
            raise ExecutorRuntimeError(result.stderr or result.stdout or "远程删除文件失败")

    def path_exists(self, path: Path) -> bool:
        result = self._run_ansible("shell", f"test -e {shlex.quote(str(path))}")
        if result.exit_code not in {0, 1}:
            raise ExecutorRuntimeError(result.stderr or result.stdout or "检查远程路径失败")
        return result.success
