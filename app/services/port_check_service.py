"""端口冲突扫描。"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional

from app.core.exceptions import PortConflictError, RemoteCommandError
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService


PORT_PATTERN = re.compile(r"(?:-Dserver\.port=|server\.port=|port=)(?P<port>\d+)")
PROGRAM_PATTERN = re.compile(r"^\[program:(?P<name>[^\]]+)\]", re.MULTILINE)


@dataclass(frozen=True)
class PortConflict:
    """端口冲突详情。"""

    source: str
    program_name: str
    port: int
    kind: str = "FILE"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PortCheckService:
    """扫描配置目录中的端口占用。"""

    def __init__(self, config_file_service: ConfigFileService, host_service: HostService):
        self.config_file_service = config_file_service
        self.host_service = host_service

    def find_conflicts(
        self,
        host: str,
        port: int,
        *,
        exclude_config_path: Optional[str] = None,
        include_runtime: bool = True,
    ) -> list[PortConflict]:
        """查找端口冲突。"""
        conflicts: list[PortConflict] = []
        # 端口扫描必须覆盖子目录和备份文件，否则初始化导入后的现场冲突会被漏掉。
        for record in self.config_file_service.list_raw_configs(host, include_backups=True, recursive=True):
            if exclude_config_path and self._should_skip(record.config_path, exclude_config_path):
                continue
            for match in PORT_PATTERN.finditer(record.content):
                current_port = int(match.group("port"))
                if current_port == port:
                    conflicts.append(
                        PortConflict(
                            source=record.config_path,
                            program_name=self._extract_program_name(record.content, record.file_name),
                            port=current_port,
                        )
                    )
                    break
        if include_runtime:
            conflicts.extend(self._find_runtime_conflicts(host, port))
        return conflicts

    def ensure_no_conflict(
        self,
        host: str,
        port: int,
        *,
        exclude_config_path: Optional[str] = None,
        include_runtime: bool = True,
    ) -> None:
        """如有冲突则抛出异常。"""
        conflicts = self.find_conflicts(
            host,
            port,
            exclude_config_path=exclude_config_path,
            include_runtime=include_runtime,
        )
        if conflicts:
            raise PortConflictError("端口冲突", [item.to_dict() for item in conflicts])

    @staticmethod
    def _should_skip(config_path: str, exclude_config_path: str) -> bool:
        backup_prefix = f"{exclude_config_path}.bak"
        return config_path == exclude_config_path or config_path.startswith(backup_prefix)

    @staticmethod
    def _extract_program_name(content: str, default_name: str) -> str:
        match = PROGRAM_PATTERN.search(content)
        if match is None:
            return default_name
        return match.group("name").strip()

    def _find_runtime_conflicts(self, host: str, port: int) -> list[PortConflict]:
        executor = self.host_service.get_executor(host)
        result = executor.run_command(["ss", "-lnutp"])
        if not result.success:
            raise RemoteCommandError(
                "读取运行端口失败",
                {"args": list(result.args), "exitCode": result.exit_code, "stdout": result.stdout, "stderr": result.stderr},
            )

        conflicts: list[PortConflict] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("netid"):
                continue
            parts = stripped.split()
            if len(parts) < 5:
                continue
            current_port = self._extract_port(parts[4])
            if current_port != port:
                continue
            conflicts.append(
                PortConflict(
                    source="ss -lnutp",
                    program_name=parts[-1],
                    port=port,
                    kind="LISTEN",
                )
            )
        return conflicts

    @staticmethod
    def _extract_port(local_address: str) -> int | None:
        if local_address.endswith("*"):
            return None
        if "]:" in local_address:
            port_text = local_address.rsplit("]:", 1)[-1]
        else:
            port_text = local_address.rsplit(":", 1)[-1]
        if not port_text.isdigit():
            return None
        return int(port_text)
