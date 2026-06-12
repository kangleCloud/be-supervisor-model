"""Ansible 执行器。"""
from __future__ import annotations

import logging
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


LOGGER = logging.getLogger(__name__)
_ANSIBLE_ENV = {
    **os.environ,
    "ANSIBLE_FORCE_COLOR": "0",
    "ANSIBLE_HOST_KEY_CHECKING": "False",
    "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.minimal",
}
_RESULT_LINE_PATTERN = re.compile(r"^(?P<host>\S+)\s+\|\s+(?P<status>SUCCESS|CHANGED|FAILED!?|UNREACHABLE!)(?P<rest>.*)$")
_STDERR_WARNING_PREFIXES = ("[WARNING]:", "[DEPRECATION WARNING]:")
_STDERR_NOISE_PREFIXES = _STDERR_WARNING_PREFIXES + ("Origin:",)


class AnsibleExecutor(RemoteExecutor):
    """通过 ansible ad-hoc 命令执行受控操作。"""

    def __init__(self, host: HostConfig, settings: ExecutorSettings):
        super().__init__(settings.ansible_timeout_seconds)
        self.host = host
        self.settings = settings

    def _base_args(self) -> list[str]:
        return [
            "ansible",
            self.host.ip,
            "-i",
            str(self.settings.ansible_inventory_path),
            "-u",
            self.settings.ansible_remote_user,
        ]

    def _inventory_error_text(self) -> str | None:
        """控制机侧先校验 inventory 可读性，避免把本地配置错误误判成远端 SSH 不可达。"""
        inventory_path = self.settings.ansible_inventory_path
        if not inventory_path.exists():
            return f"Ansible inventory 不存在或不可读: {inventory_path}"
        if not os.access(inventory_path, os.R_OK):
            return f"Ansible inventory 不存在或不可读: {inventory_path}"
        return None

    def _run_ansible(self, module: str, module_args: str, timeout: int | None = None) -> CommandResult:
        command = self._base_args() + ["-m", module, "-a", module_args]
        inventory_error = self._inventory_error_text()
        if inventory_error is not None:
            return CommandResult(tuple(command), 2, "", inventory_error)
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
        raw_combined = f"{stdout}\n{stderr}".lower()
        normalized_stderr, ignored_warning_text = self._normalize_stderr(stderr)
        if ignored_warning_text:
            # Ansible 2.21+ 会把 callback/deprecation 告警写到 stderr，这些噪音不能污染接口失败文案。
            LOGGER.debug("ignore ansible stderr warnings for host=%s: %s", self.host.ip, ignored_warning_text)

        if "could not match supplied host pattern" in raw_combined or "no hosts matched" in raw_combined:
            return CommandResult(
                tuple(command), 1, stdout,
                f"目标主机未匹配: ansible inventory 中未找到 {self.host.ip}",
            )

        return CommandResult(tuple(command), proc.returncode, stdout, normalized_stderr)

    @staticmethod
    def _split_payload_lines(payload: str) -> list[str]:
        """兼容 oneline 回调把多行 stdout 折叠成 \n 文本的场景。"""
        normalized = payload.replace("\\n", "\n").replace("\\r", "\r")
        return [line.strip() for line in normalized.splitlines() if line.strip()]

    @staticmethod
    def _normalize_stderr(stderr: str) -> tuple[str, str]:
        """剥离 warning 噪音，保留真正的错误正文。"""
        kept_lines: list[str] = []
        ignored_lines: list[str] = []
        for raw_line in stderr.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith(_STDERR_NOISE_PREFIXES):
                ignored_lines.append(stripped)
                continue
            kept_lines.append(stripped)
        return "\n".join(kept_lines).strip(), "\n".join(ignored_lines).strip()

    def _extract_stdout_lines(self, stdout: str) -> list[str]:
        """提取 ansible minimal/default 输出中的真实 stdout 负载，避免把主机前缀误当成业务内容。"""
        payload_lines: list[str] = []
        reading_result_payload = False
        for raw_line in stdout.splitlines():
            line = raw_line.rstrip("\r")
            stripped = line.strip()
            if not stripped:
                continue

            match = _RESULT_LINE_PATTERN.match(stripped)
            if match is None:
                payload_lines.extend(self._split_payload_lines(line if reading_result_payload else stripped))
                continue

            reading_result_payload = False
            rest = match.group("rest").strip()
            if "(stdout)" in rest:
                payload_lines.extend(self._split_payload_lines(rest.split("(stdout)", 1)[1].strip()))
                continue
            if ">>" in rest:
                inline_payload = rest.split(">>", 1)[1].strip()
                if inline_payload:
                    payload_lines.extend(self._split_payload_lines(inline_payload))
                reading_result_payload = True
                continue
            if "=>" in rest:
                # FAILED/UNREACHABLE 等结果会把真实错误正文放在 => 后面，失败时必须保留这段内容。
                inline_payload = rest.split("=>", 1)[1].strip()
                if inline_payload:
                    payload_lines.extend(self._split_payload_lines(inline_payload))
                reading_result_payload = True
                continue

        return payload_lines

    def _build_error_text(self, result: CommandResult, default_message: str) -> str:
        """失败时优先返回清洗后的 stderr，其次返回结果头中的真实失败正文。"""
        parsed_stdout = "\n".join(self._extract_stdout_lines(result.stdout)).strip()
        return result.stderr or parsed_stdout or result.stdout.strip() or default_message

    def run_command(self, command: Sequence[str], timeout: int | None = None) -> CommandResult:
        result = self._run_ansible("shell", shlex.join(list(command)), timeout=timeout)
        parsed_stdout = "\n".join(self._extract_stdout_lines(result.stdout)).strip()
        return CommandResult(
            result.args,
            result.exit_code,
            parsed_stdout or result.stdout.strip(),
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
            raise ExecutorRuntimeError(self._build_error_text(result, "列出配置文件失败"))
        return [Path(line) for line in sorted(self._extract_stdout_lines(result.stdout))]

    def read_text(self, path: Path) -> str:
        result = self._run_ansible("shell", f"cat {shlex.quote(str(path))}")
        if not result.success:
            raise ExecutorRuntimeError(self._build_error_text(result, "读取远程文件失败"))
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
                raise ExecutorRuntimeError(self._build_error_text(copy_result, "远程复制文件失败"))
            move_result = self._run_ansible(
                "shell",
                f"mv {shlex.quote(remote_temp)} {shlex.quote(str(path))}",
            )
            if not move_result.success:
                raise ExecutorRuntimeError(self._build_error_text(move_result, "远程原子写入失败"))
        finally:
            Path(local_temp).unlink(missing_ok=True)

    def copy_file(self, source: Path, target: Path) -> None:
        result = self._run_ansible("shell", f"cp {shlex.quote(str(source))} {shlex.quote(str(target))}")
        if not result.success:
            raise ExecutorRuntimeError(self._build_error_text(result, "远程复制文件失败"))

    def move_file(self, source: Path, target: Path) -> None:
        result = self._run_ansible("shell", f"mv {shlex.quote(str(source))} {shlex.quote(str(target))}")
        if not result.success:
            raise ExecutorRuntimeError(self._build_error_text(result, "远程移动文件失败"))

    def remove_file(self, path: Path, missing_ok: bool = False) -> None:
        flag = "-f " if missing_ok else ""
        result = self._run_ansible("shell", f"rm {flag}{shlex.quote(str(path))}")
        if not result.success:
            raise ExecutorRuntimeError(self._build_error_text(result, "远程删除文件失败"))

    def path_exists(self, path: Path) -> bool:
        result = self._run_ansible("shell", f"test -e {shlex.quote(str(path))}")
        if result.exit_code not in {0, 1}:
            raise ExecutorRuntimeError(self._build_error_text(result, "检查远程路径失败"))
        return result.success
