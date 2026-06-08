"""主机白名单与执行器选择。"""
from __future__ import annotations

from app.core.config import HostConfig, Settings
from app.core.exceptions import ForbiddenOperationError, InvalidHostError
from app.core.security import ensure_safe_host
from app.executor.ansible import AnsibleExecutor
from app.executor.base import RemoteExecutor
from app.executor.local import LocalExecutor


class HostService:
    """主机解析服务。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def list_hosts(self) -> list[dict[str, object]]:
        """返回可用主机列表。"""
        return [
            {
                "name": host.name,
                "ip": host.ip,
                "enabled": host.enabled,
                "executorType": host.executor_type,
                "ansiblePattern": host.ansible_pattern,
            }
            for host in self.settings.hosts
        ]

    def get_host(self, host_value: str) -> HostConfig:
        """根据 IP 获取主机配置。"""
        safe_host = ensure_safe_host(host_value)
        for host in self.settings.hosts:
            if host.ip == safe_host:
                if not host.enabled:
                    raise InvalidHostError("目标主机已被禁用")
                return host
        raise InvalidHostError()

    def ensure_mutation_allowed(self, host_value: str, forbidden_msg: str) -> HostConfig:
        """当前项目约束：远端 ansible 主机只允许读，不允许通过服务端改现场。"""
        host = self.get_host(host_value)
        if host.executor_type == "ansible":
            raise ForbiddenOperationError(forbidden_msg)
        return host

    def get_executor(self, host_value: str) -> RemoteExecutor:
        """按主机配置创建执行器。"""
        host = self.get_host(host_value)
        if host.executor_type == "local":
            return LocalExecutor(self.settings.supervisor.command_timeout_seconds)
        return AnsibleExecutor(host, self.settings.executor)
