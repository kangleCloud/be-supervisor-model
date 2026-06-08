"""执行器抽象。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class CommandResult:
    """命令执行结果。"""

    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class ExecutorRuntimeError(RuntimeError):
    """执行器底层错误。"""


class RemoteExecutor(ABC):
    """受控远程执行器。"""

    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds

    @abstractmethod
    def run_command(self, command: Sequence[str], timeout: int | None = None) -> CommandResult:
        """执行受控命令。"""

    @abstractmethod
    def list_configs(self, conf_dir: Path, *, recursive: bool = False, include_backups: bool = True) -> list[Path]:
        """列出配置目录下的配置与备份文件。"""

    @abstractmethod
    def read_text(self, path: Path) -> str:
        """读取文本文件。"""

    @abstractmethod
    def write_text_atomic(self, path: Path, content: str) -> None:
        """原子写入文本。"""

    @abstractmethod
    def copy_file(self, source: Path, target: Path) -> None:
        """复制文件。"""

    @abstractmethod
    def move_file(self, source: Path, target: Path) -> None:
        """移动文件。"""

    @abstractmethod
    def remove_file(self, path: Path, missing_ok: bool = False) -> None:
        """删除文件。"""

    @abstractmethod
    def path_exists(self, path: Path) -> bool:
        """检查路径是否存在。"""
