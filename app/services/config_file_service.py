"""Supervisor 配置文件安全操作。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import ConfigAlreadyExistsError, ConfigNotFoundError, FileOperationError
from app.core.security import (
    ensure_safe_program_name,
    ensure_safe_path_under_dir,
    normalize_config_name,
    normalize_config_path,
)
from app.executor.base import ExecutorRuntimeError
from app.services.host_service import HostService
from app.services.template_service import ParsedConfig, TemplateService


@dataclass(frozen=True)
class RawConfig:
    """原始配置文件内容。"""

    config_path: str
    file_name: str
    config_name: str
    path: str
    content: str


@dataclass(frozen=True)
class StoredConfig:
    """存储在目标主机上的可解析配置。"""

    config_path: str
    file_name: str
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

    def build_config_path_from_relative(self, config_path: str, *, allow_backups: bool = False) -> Path:
        normalized_path = normalize_config_path(config_path, allow_backups=allow_backups)
        target = self._conf_dir() / normalized_path
        return ensure_safe_path_under_dir(self._conf_dir(), target)

    def _backup_path(self, config_path: Path) -> Path:
        return Path(f"{config_path}.bak")

    def _ensure_remote_write_allowed(self, host: str) -> None:
        """远端主机当前只允许读取配置，不允许通过本服务改动文件现场。"""
        self.host_service.ensure_mutation_allowed(host, "当前项目禁止修改远端配置文件")

    @staticmethod
    def _path_exists(executor, path: Path, error_msg: str) -> bool:
        try:
            return executor.path_exists(path)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(error_msg) from exc

    @staticmethod
    def _timestamp_suffix(now: datetime | None = None) -> str:
        current = now or datetime.now()
        return current.strftime("%Y%m%d-%H%M%S")

    def _relative_config_path(self, path: Path) -> str:
        safe_path = ensure_safe_path_under_dir(self._conf_dir(), path)
        return safe_path.relative_to(self._conf_dir()).as_posix()

    def _build_raw_config(self, path: Path, content: str) -> RawConfig:
        config_path = self._relative_config_path(path)
        return RawConfig(
            config_path=config_path,
            file_name=path.name,
            config_name=path.name,
            path=str(path),
            content=content,
        )

    def _read_raw_config_by_path(self, host: str, config_path: Path) -> RawConfig:
        executor = self.host_service.get_executor(host)
        try:
            content = executor.read_text(config_path)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"读取配置文件失败: {config_path.name}") from exc
        return self._build_raw_config(config_path, content)

    def list_config_paths(self, host: str, include_backups: bool = True, *, recursive: bool = False) -> list[Path]:
        """列出目标主机上的配置路径。"""
        executor = self.host_service.get_executor(host)
        try:
            return executor.list_configs(self._conf_dir(), recursive=recursive, include_backups=include_backups)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"读取配置目录失败: {exc}") from exc

    def list_raw_configs(self, host: str, include_backups: bool = True, *, recursive: bool = False) -> list[RawConfig]:
        """读取目标主机上的原始配置内容。"""
        result: list[RawConfig] = []
        for path in self.list_config_paths(host, include_backups=include_backups, recursive=recursive):
            result.append(self._read_raw_config_by_path(host, path))
        return result

    def list_configs(self, host: str, include_backups: bool = True, *, recursive: bool = False) -> list[StoredConfig]:
        """列出目标主机上的可解析配置文件。"""
        result: list[StoredConfig] = []
        for item in self.list_raw_configs(host, include_backups=include_backups, recursive=recursive):
            result.append(
                StoredConfig(
                    config_path=item.config_path,
                    file_name=item.file_name,
                    config_name=item.config_name,
                    path=item.path,
                    content=item.content,
                    parsed=self.template_service.parse(item.content),
                )
            )
        return result

    def find_by_program_name(self, host: str, program_name: str, include_backups: bool = False) -> StoredConfig:
        """按 programName 查找配置。"""
        safe_program_name = ensure_safe_program_name(program_name)
        for record in self.list_configs(host, include_backups=include_backups, recursive=True):
            if record.parsed.program_name == safe_program_name:
                return record
        raise ConfigNotFoundError(f"未找到服务 {safe_program_name}")

    def exists(self, host: str, config_name: str, program_name: str | None = None) -> bool:
        """检查顶层配置是否存在。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        return self._path_exists(executor, config_path, f"检查配置文件是否存在失败: {config_path.name}")

    def exists_by_config_path(self, host: str, config_path: str, *, allow_backups: bool = False) -> bool:
        """按相对路径检查配置是否存在。"""
        executor = self.host_service.get_executor(host)
        absolute_path = self.build_config_path_from_relative(config_path, allow_backups=allow_backups)
        return self._path_exists(executor, absolute_path, f"检查配置文件是否存在失败: {absolute_path.name}")

    def read_raw_config(self, host: str, config_name: str, program_name: str | None = None) -> RawConfig:
        """读取顶层配置的原始文本。"""
        config_path = self.build_config_path(config_name, program_name)
        if not self.exists(host, config_name, program_name):
            raise ConfigNotFoundError(f"配置文件不存在: {config_path.name}")
        return self._read_raw_config_by_path(host, config_path)

    def read_raw_config_optional(self, host: str, config_name: str, program_name: str | None = None) -> RawConfig | None:
        """读取顶层配置，不存在时返回 None。"""
        config_path = self.build_config_path(config_name, program_name)
        if not self.exists(host, config_name, program_name):
            return None
        return self._read_raw_config_by_path(host, config_path)

    def read_raw_config_by_config_path(self, host: str, config_path: str, *, allow_backups: bool = False) -> RawConfig:
        """按相对路径读取配置原文。"""
        absolute_path = self.build_config_path_from_relative(config_path, allow_backups=allow_backups)
        if not self.exists_by_config_path(host, config_path, allow_backups=allow_backups):
            raise ConfigNotFoundError(f"配置文件不存在: {config_path}")
        return self._read_raw_config_by_path(host, absolute_path)

    def read_raw_config_optional_by_config_path(
        self,
        host: str,
        config_path: str,
        *,
        allow_backups: bool = False,
    ) -> RawConfig | None:
        """按相对路径读取配置，不存在时返回 None。"""
        absolute_path = self.build_config_path_from_relative(config_path, allow_backups=allow_backups)
        if not self.exists_by_config_path(host, config_path, allow_backups=allow_backups):
            return None
        return self._read_raw_config_by_path(host, absolute_path)

    def read_config(self, host: str, config_name: str, program_name: str | None = None) -> StoredConfig:
        """读取顶层配置。"""
        raw_config = self.read_raw_config(host, config_name, program_name=program_name)
        return StoredConfig(
            config_path=raw_config.config_path,
            file_name=raw_config.file_name,
            config_name=raw_config.config_name,
            path=raw_config.path,
            content=raw_config.content,
            parsed=self.template_service.parse(raw_config.content),
        )

    def write_config(self, host: str, config_name: str, content: str, program_name: str | None = None) -> str:
        """原子写入顶层配置。"""
        self._ensure_remote_write_allowed(host)
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        try:
            executor.write_text_atomic(config_path, content)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"写入配置文件失败: {config_path.name}") from exc
        return str(config_path)

    def ensure_not_exists(self, host: str, config_name: str, program_name: str | None = None) -> Path:
        """确保目标顶层配置尚不存在。"""
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        if self._path_exists(executor, config_path, f"检查配置文件是否存在失败: {config_path.name}"):
            raise ConfigAlreadyExistsError(f"配置文件已存在: {config_path.name}")
        return config_path

    def backup_config(self, host: str, config_name: str, program_name: str | None = None) -> dict[str, str]:
        """创建或覆盖 .bak 备份。"""
        self._ensure_remote_write_allowed(host)
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        backup_path = self._backup_path(config_path)
        if not self._path_exists(executor, config_path, f"检查配置文件失败: {config_path.name}"):
            raise ConfigNotFoundError(f"配置文件不存在: {config_path.name}")

        archived_backup: str | None = None
        try:
            if self._path_exists(executor, backup_path, f"检查备份文件失败: {backup_path.name}"):
                archived_path = Path(f"{backup_path}.{self._timestamp_suffix()}")
                executor.move_file(backup_path, archived_path)
                archived_backup = str(archived_path)
            executor.copy_file(config_path, backup_path)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"备份配置失败: {config_path.name}") from exc

        return {"configPath": str(config_path), "backupPath": str(backup_path), "archivedBackupPath": archived_backup}

    def restore_config(self, host: str, config_name: str, program_name: str | None = None) -> dict[str, str | None]:
        """从 .bak 还原配置。"""
        self._ensure_remote_write_allowed(host)
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        backup_path = self._backup_path(config_path)
        if not self._path_exists(executor, backup_path, f"检查备份文件失败: {backup_path.name}"):
            raise ConfigNotFoundError(f"备份文件不存在: {backup_path.name}")

        archived_current: str | None = None
        try:
            if self._path_exists(executor, config_path, f"检查配置文件失败: {config_path.name}"):
                archived_current_path = Path(f"{config_path}.{self._timestamp_suffix()}")
                executor.move_file(config_path, archived_current_path)
                archived_current = str(archived_current_path)
            executor.copy_file(backup_path, config_path)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"还原配置失败: {config_path.name}") from exc

        return {"configPath": str(config_path), "backupPath": str(backup_path), "archivedCurrentPath": archived_current}

    def delete_config(
        self,
        host: str,
        config_name: str,
        delete_backup: bool = False,
        program_name: str | None = None,
    ) -> dict[str, str | None]:
        """删除主配置文件，按需删除备份。"""
        self._ensure_remote_write_allowed(host)
        executor = self.host_service.get_executor(host)
        config_path = self.build_config_path(config_name, program_name)
        backup_path = self._backup_path(config_path)
        if not self._path_exists(executor, config_path, f"检查配置文件失败: {config_path.name}"):
            raise ConfigNotFoundError(f"配置文件不存在: {config_path.name}")
        try:
            executor.remove_file(config_path)
            removed_backup = None
            if delete_backup and self._path_exists(executor, backup_path, f"检查备份文件失败: {backup_path.name}"):
                executor.remove_file(backup_path)
                removed_backup = str(backup_path)
        except ExecutorRuntimeError as exc:
            raise FileOperationError(f"删除配置失败: {config_path.name}") from exc
        return {"configPath": str(config_path), "removedBackupPath": removed_backup}
