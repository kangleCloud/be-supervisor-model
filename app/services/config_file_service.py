"""Supervisor 配置文件安全操作。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import ConfigAlreadyExistsError, ConfigNotFoundError, FileOperationError
from app.core.security import ensure_safe_program_name, ensure_safe_path_under_dir, normalize_config_name
from app.executor.base import ExecutorRuntimeError
from app.services.host_service import HostService
from app.services.template_service import ParsedConfig, TemplateService


@dataclass(frozen=True)
class StoredConfig:
    """存储在目标主机上的配置。"""

    config_name: str
    path: str
    content: str
    parsed: ParsedConfig


class ConfigFileService:
    """对配置与备份文件进行受控操作。"""

    def __init__(self, settings: Settings, host_service: HostService, template_service: TemplateService):
        self.settings = settings
        self.host_service = host_service
        self.template_service = template_service

    def _conf_dir(self) -> Path:
        return self.settings.supervisor.conf_dir

    def build_config_path(self, config_name: str, program_name: str | None = None) -> Path:
        normalized_name = normalize_config_name(config_name, program_name)
        target = self._conf_dir() / normalized_name
        return ensure_safe_path_under_dir(self._conf_dir(), target)

    def _backup_path(self, config_path: Path) -> Path:
        return Path(f"{config_path}.bak")

    @staticmethod
    def _timestamp_suffix(now: datetime | None = None) -> str:
        current = now or datetime.now()
        return current.strftime("%Y%m%d-%H%M%S")

    def list_configs(self, host: str, include_backups: bool = True) -> list[StoredConfig]:
        """列出目标主机上的配置文件。"""
        executor = self.host_service.get_executor(host)
        try:
            paths = executor.list_configs(self._conf_dir())
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"读取配置目录失败: {exc}") from exc

        result: list[StoredConfig] = []
        for path in paths:
            if not include_backups and not path.name.endswith(".ini"):
                continue
            content = executor.read_text(path)
            parsed = self.template_service.parse(content)
            result.append(
                StoredConfig(
                    config_name=path.name,
                    path=str(path),
                    content=content,
                    parsed=parsed,
                )
            )
        return result

    def find_by_program_name(self, host: str, program_name: str, include_backups: bool = False) -> StoredConfig:
        """按 programName 查找配置。"""
        safe_program_name = ensure_safe_program_name(program_name)
        for record in self.list_configs(host, include_backups=include_backups):
            if record.parsed.program_name == safe_program_name:
                return record
        raise ConfigNotFoundError(f"未找到服务 {safe_program_name}")

    def exists(self, host: str, config_name: str, program_name: str | None = None) -> bool:
        """检查配置是否存在。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        return executor.path_exists(config_path)

    def read_config(self, host: str, config_name: str, program_name: str | None = None) -> StoredConfig:
        """读取指定配置。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        if not executor.path_exists(config_path):
            raise ConfigNotFoundError(f"配置文件不存在: {config_path.name}")
        content = executor.read_text(config_path)
        return StoredConfig(
            config_name=config_path.name,
            path=str(config_path),
            content=content,
            parsed=self.template_service.parse(content),
        )

    def write_config(self, host: str, config_name: str, content: str, program_name: str | None = None) -> str:
        """原子写入配置。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        try:
            executor.write_text_atomic(config_path, content)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"写入配置文件失败: {config_path.name}") from exc
        return str(config_path)

    def ensure_not_exists(self, host: str, config_name: str, program_name: str | None = None) -> Path:
        """确保目标配置尚不存在。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        if executor.path_exists(config_path):
            raise ConfigAlreadyExistsError(f"配置文件已存在: {config_path.name}")
        return config_path

    def backup_config(self, host: str, config_name: str, program_name: str | None = None) -> dict[str, str]:
        """创建或覆盖 .bak 备份。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        backup_path = self._backup_path(config_path)
        if not executor.path_exists(config_path):
            raise ConfigNotFoundError(f"配置文件不存在: {config_path.name}")

        archived_backup: str | None = None
        try:
            if executor.path_exists(backup_path):
                archived_path = Path(f"{backup_path}.{self._timestamp_suffix()}")
                executor.move_file(backup_path, archived_path)
                archived_backup = str(archived_path)
            executor.copy_file(config_path, backup_path)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"备份配置失败: {config_path.name}") from exc

        return {"configPath": str(config_path), "backupPath": str(backup_path), "archivedBackupPath": archived_backup}

    def restore_config(self, host: str, config_name: str, program_name: str | None = None) -> dict[str, str | None]:
        """从 .bak 还原配置。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        backup_path = self._backup_path(config_path)
        if not executor.path_exists(backup_path):
            raise ConfigNotFoundError(f"备份文件不存在: {backup_path.name}")

        archived_current: str | None = None
        try:
            if executor.path_exists(config_path):
                archived_current_path = Path(f"{config_path}.{self._timestamp_suffix()}")
                executor.move_file(config_path, archived_current_path)
                archived_current = str(archived_current_path)
            executor.copy_file(backup_path, config_path)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"还原配置失败: {config_path.name}") from exc

        return {"configPath": str(config_path), "backupPath": str(backup_path), "archivedCurrentPath": archived_current}

    def delete_config(self, host: str, config_name: str, delete_backup: bool = False, program_name: str | None = None) -> dict[str, str | None]:
        """删除主配置文件，按需删除备份。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        backup_path = self._backup_path(config_path)
        if not executor.path_exists(config_path):
            raise ConfigNotFoundError(f"配置文件不存在: {config_path.name}")
        try:
            executor.remove_file(config_path)
            removed_backup = None
            if delete_backup and executor.path_exists(backup_path):
                executor.remove_file(backup_path)
                removed_backup = str(backup_path)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"删除配置失败: {config_path.name}") from exc
        return {"configPath": str(config_path), "removedBackupPath": removed_backup}
