"""端口冲突扫描。"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional

from app.core.exceptions import PortConflictError
from app.core.security import normalize_config_name
from app.services.config_file_service import ConfigFileService


PORT_PATTERN = re.compile(r"(?:-Dserver\.port=|server\.port=|port=)(?P<port>\d+)")
PROGRAM_PATTERN = re.compile(r"^\[program:(?P<name>[^\]]+)\]", re.MULTILINE)


@dataclass(frozen=True)
class PortConflict:
    """端口冲突详情。"""

    file_path: str
    program_name: str
    port: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PortCheckService:
    """扫描配置目录中的端口占用。"""

    def __init__(self, config_file_service: ConfigFileService):
        self.config_file_service = config_file_service

    def find_conflicts(self, host: str, port: int, exclude_config: Optional[str] = None) -> list[PortConflict]:
        """查找端口冲突。"""
        normalized_exclude = normalize_config_name(exclude_config, exclude_config) if exclude_config else None
        conflicts: list[PortConflict] = []
        for record in self.config_file_service.list_raw_configs(host, include_backups=True):
            if normalized_exclude and self._should_skip(record.config_name, normalized_exclude):
                continue
            for match in PORT_PATTERN.finditer(record.content):
                current_port = int(match.group("port"))
                if current_port == port:
                    conflicts.append(
                        PortConflict(
                            file_path=record.path,
                            program_name=self._extract_program_name(record.content, record.config_name),
                            port=current_port,
                        )
                    )
                    break
        return conflicts

    def ensure_no_conflict(self, host: str, port: int, exclude_config: Optional[str] = None) -> None:
        """如有冲突则抛出异常。"""
        conflicts = self.find_conflicts(host, port, exclude_config=exclude_config)
        if conflicts:
            raise PortConflictError("端口冲突", [item.to_dict() for item in conflicts])

    @staticmethod
    def _should_skip(file_name: str, exclude_config_name: str) -> bool:
        backup_prefix = f"{exclude_config_name}.bak"
        return file_name == exclude_config_name or file_name.startswith(backup_prefix)

    @staticmethod
    def _extract_program_name(content: str, default_name: str) -> str:
        match = PROGRAM_PATTERN.search(content)
        if match is None:
            return default_name
        return match.group("name")
