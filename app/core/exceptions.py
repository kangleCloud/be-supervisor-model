"""业务异常定义。"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """应用级业务异常。"""

    def __init__(self, http_status: int, code: int, msg: str, data: Any = None):
        super().__init__(msg)
        self.http_status = http_status
        self.code = code
        self.msg = msg
        self.data = data


class ParamError(AppError):
    """请求参数错误。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(400, 40000, msg, data)


class UnauthorizedError(AppError):
    """未授权或登录失效。"""

    def __init__(self, msg: str = "未登录或登录已过期", data: Any = None):
        super().__init__(401, 40100, msg, data)


class ForbiddenOperationError(AppError):
    """当前环境禁止执行的业务操作。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(403, 40300, msg, data)


class InvalidHostError(AppError):
    """非法目标主机。"""

    def __init__(self, msg: str = "目标主机不在白名单中", data: Any = None):
        super().__init__(400, 40001, msg, data)


class InvalidConfigNameError(AppError):
    """非法配置名称。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(400, 40002, msg, data)


class PortConflictError(AppError):
    """端口冲突。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(409, 40003, msg, data)


class ConfigNotFoundError(AppError):
    """配置文件不存在。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(404, 40400, msg, data)


class ConfigAlreadyExistsError(AppError):
    """配置文件已存在。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(409, 40900, msg, data)


class ArchivedServiceOperationError(AppError):
    """归档服务禁止执行运行操作。"""

    def __init__(self, msg: str = "服务已归档，不能执行启动、停止或重启操作", data: Any = None):
        super().__init__(409, 40910, msg, data)


class ArchiveStateError(AppError):
    """归档 / 还原状态不满足前置条件。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(409, 40911, msg, data)


class InternalError(AppError):
    """内部错误。"""

    def __init__(self, msg: str = "服务内部错误", data: Any = None):
        super().__init__(500, 50000, msg, data)


class RemoteCommandError(AppError):
    """远程命令执行失败。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(500, 50010, msg, data)


class SupervisorCommandError(AppError):
    """Supervisor 命令执行失败。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(500, 50020, msg, data)


class FileOperationError(AppError):
    """文件操作失败。"""

    def __init__(self, msg: str, data: Any = None):
        super().__init__(500, 50030, msg, data)
