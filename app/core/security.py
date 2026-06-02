"""输入安全校验。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.exceptions import InvalidConfigNameError, ParamError


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


def ensure_safe_path_under_dir(base_dir: Path, target: Path) -> Path:
    """确保目标路径仍位于允许目录内。"""
    base_resolved = base_dir.resolve()
    target_resolved = target.resolve()
    if base_resolved not in target_resolved.parents and target_resolved != base_resolved:
        raise InvalidConfigNameError("目标路径超出允许目录")
    return target_resolved


def ensure_valid_port(port: int) -> int:
    """校验端口范围。"""
    if port < 1 or port > 65535:
        raise ParamError("port 必须在 1-65535 范围内")
    return port


def verify_api_token_dependency(
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    settings: Settings = Depends(get_settings),
) -> None:
    """预留 API Token 鉴权扩展点。"""
    if not settings.app.api_token:
        return

    if not x_api_token:
        raise ParamError("缺少 API Token")
    if x_api_token != settings.app.api_token:
        raise ParamError("API Token 无效")
