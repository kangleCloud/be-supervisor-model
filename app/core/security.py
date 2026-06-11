"""输入安全校验。"""
from __future__ import annotations

import os.path
import re
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.exceptions import InvalidConfigNameError, ParamError, UnauthorizedError
from app.services.auth_service import AuthService, AuthenticatedUser


SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")
PROGRAM_NAME_PATTERN = SAFE_NAME_PATTERN
HOST_PATTERN = re.compile(r"^[a-zA-Z0-9_.:-]+$")


def ensure_safe_name(value: str, field_name: str) -> str:
    """校验通用安全名称。"""
    raw_value = (value or "").strip()
    if not raw_value:
        raise ParamError(f"{field_name} 不能为空")
    if not SAFE_NAME_PATTERN.fullmatch(raw_value):
        raise ParamError(f"{field_name} 只允许字母、数字、点、下划线和中划线")
    return raw_value


def ensure_safe_program_name(value: str) -> str:
    """校验 Supervisor programName。"""
    raw_value = (value or "").strip()
    if not raw_value:
        raise ParamError("programName 不能为空")
    if not PROGRAM_NAME_PATTERN.fullmatch(raw_value):
        raise ParamError("programName 不符合安全规则")
    return raw_value


def ensure_safe_host(value: str) -> str:
    """校验主机字段字面量格式。"""
    raw_value = (value or "").strip()
    if not raw_value:
        raise ParamError("host 不能为空")
    if not HOST_PATTERN.fullmatch(raw_value):
        raise ParamError("host 格式非法")
    return raw_value


def normalize_config_name(config_name: str, program_name: Optional[str] = None) -> str:
    """规范化配置文件名，只允许生成 *.ini。"""
    raw_value = (config_name or "").strip()
    if not raw_value:
        if not program_name:
            raise InvalidConfigNameError("configName 不能为空")
        raw_value = program_name

    if raw_value.endswith(".ini"):
        raw_value = raw_value[:-4]

    if "/" in raw_value or "\\" in raw_value or ".." in raw_value:
        raise InvalidConfigNameError("configName 不允许包含路径")

    if raw_value.endswith(".bak") or ".bak." in raw_value:
        raise InvalidConfigNameError("configName 不允许使用备份文件名")

    ensure_safe_name(raw_value, "configName")
    return f"{raw_value}.ini"


def normalize_config_path(config_path: str, *, allow_backups: bool = False) -> str:
    """规范化相对配置路径，允许子目录但禁止穿越。"""
    raw_value = (config_path or "").replace("\\", "/").strip().strip("/")
    if not raw_value:
        raise InvalidConfigNameError("configPath 不能为空")

    normalized = os.path.normpath(raw_value).replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        raise InvalidConfigNameError("configPath 不能为空")

    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidConfigNameError("configPath 不允许包含路径穿越")

    for part in parts[:-1]:
        ensure_safe_name(part, "configPath")

    file_name = parts[-1]
    if allow_backups:
        if not (
            file_name.endswith(".ini")
            or file_name.endswith(".ini.bak")
            or ".ini.bak." in file_name
        ):
            raise InvalidConfigNameError("configPath 只允许 .ini 或备份文件")
        base_name = file_name.split(".ini", 1)[0]
        ensure_safe_name(base_name, "configPath")
        return normalized

    if not file_name.endswith(".ini"):
        raise InvalidConfigNameError("configPath 只允许 .ini 文件")
    if file_name.endswith(".bak") or ".bak." in file_name:
        raise InvalidConfigNameError("configPath 不允许使用备份文件名")

    ensure_safe_name(file_name[:-4], "configPath")
    return normalized


def ensure_safe_path_under_dir(base_dir: Path, target: Path) -> Path:
    """确保目标路径仍位于允许目录内。"""
    # 这里不能依赖 resolve()，否则控制机上的 /etc 会被折叠成 /private/etc，破坏远端 Linux 的路径语义。
    base_normalized = Path(os.path.normpath(str(base_dir)))
    target_normalized = Path(os.path.normpath(str(target)))
    if not base_normalized.is_absolute() or not target_normalized.is_absolute():
        raise InvalidConfigNameError("目标路径必须是绝对路径")
    if base_normalized not in target_normalized.parents and target_normalized != base_normalized:
        raise InvalidConfigNameError("目标路径超出允许目录")
    return target_normalized


def ensure_valid_port(port: int) -> int:
    """校验端口范围。"""
    if port < 1 or port > 65535:
        raise ParamError("port 必须在 1-65535 范围内")
    return port


def extract_bearer_token(authorization: Optional[str]) -> str:
    """从 Authorization 头中提取 Bearer Token。"""
    raw_value = (authorization or "").strip()
    if not raw_value:
        raise UnauthorizedError("缺少登录凭证")
    token_type, _, token = raw_value.partition(" ")
    if token_type.lower() != "bearer" or not token.strip():
        raise UnauthorizedError("Authorization 头格式必须为 Bearer Token")
    return token.strip()


async def verify_jwt_dependency(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """统一校验 Bearer JWT，并绑定服务端会话。"""
    token = extract_bearer_token(authorization)
    return await AuthService(settings).authenticate_access_token(token)
