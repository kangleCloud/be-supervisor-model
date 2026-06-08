"""本地执行器。"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Sequence

from app.executor.base import CommandResult, ExecutorRuntimeError, RemoteExecutor


class LocalExecutor(RemoteExecutor):
    """本机文件与命令执行实现。"""

    def run_command(self, command: Sequence[str], timeout: int | None = None) -> CommandResult:
        try:
            proc = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            return CommandResult(tuple(command), 127, "", str(exc))
        except subprocess.TimeoutExpired as exc:
            return CommandResult(tuple(command), 124, exc.stdout or "", exc.stderr or "命令执行超时")

        return CommandResult(tuple(command), proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip())

    def list_configs(self, conf_dir: Path, *, recursive: bool = False, include_backups: bool = True) -> list[Path]:
        if not conf_dir.exists():
            return []

        globber = conf_dir.rglob if recursive else conf_dir.glob
        paths = list(globber("*.ini"))
        if include_backups:
            paths.extend(globber("*.ini.bak"))
            paths.extend(globber("*.ini.bak.*"))
        return sorted(path for path in paths if path.is_file())

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_text_atomic(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
        try:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(path)
        except OSError as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise ExecutorRuntimeError(str(exc)) from exc

    def copy_file(self, source: Path, target: Path) -> None:
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise ExecutorRuntimeError(str(exc)) from exc

    def move_file(self, source: Path, target: Path) -> None:
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            raise ExecutorRuntimeError(str(exc)) from exc

    def remove_file(self, path: Path, missing_ok: bool = False) -> None:
        try:
            path.unlink(missing_ok=missing_ok)
        except OSError as exc:
            raise ExecutorRuntimeError(str(exc)) from exc

    def path_exists(self, path: Path) -> bool:
        return path.exists()
