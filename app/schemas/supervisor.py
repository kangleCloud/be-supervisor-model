"""Supervisor API 请求模型。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import ensure_safe_name, ensure_valid_port


class HostRequest(BaseModel):
    """仅携带目标主机的请求。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    host: str = Field(..., description="目标主机 IP")


class ServiceUpsertRequest(BaseModel):
    """新增或修改服务请求。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    host: str = Field(..., description="目标主机 IP")
    job_name: str = Field(..., alias="jobName", description="业务作业名称")
    module_name: str = Field(..., alias="moduleName", description="模块名称")
    java_path: str = Field(..., alias="javaPath", description="Java 可执行文件绝对路径")
    active: str = Field(..., description="Spring profile 环境")
    port: int = Field(..., description="服务监听端口")
    jar_name: str = Field(default="", alias="jarName", description="Jar 包文件名，默认使用 moduleName.jar")
    config_name: str = Field(default="", alias="configName", description="配置文件名，可为空")
    xms: str = Field(default="128m", description="JVM Xms 参数")
    xmx: str = Field(default="128m", description="JVM Xmx 参数")
    user: str = Field(default="root", description="Supervisor 运行用户")
    auto_start: bool = Field(default=False, alias="autoStart", description="配置变更完成后是否立即启动服务")

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        return ensure_valid_port(value)

    @field_validator("job_name")
    @classmethod
    def validate_job_name(cls, value: str) -> str:
        return ensure_safe_name(value, "jobName")

    @field_validator("module_name")
    @classmethod
    def validate_module_name(cls, value: str) -> str:
        return ensure_safe_name(value, "moduleName")

    @field_validator("jar_name")
    @classmethod
    def validate_jar_name(cls, value: str) -> str:
        if not value:
            return value
        return ensure_safe_name(value, "jarName")

    @field_validator("java_path", "active", "xms", "xmx", "user")
    @classmethod
    def validate_required_text(cls, value: str, info) -> str:
        raw_value = (value or "").strip()
        if not raw_value:
            raise ValueError(f"{info.field_name} 不能为空")
        return raw_value


class DeleteServiceQuery(BaseModel):
    """删除服务请求参数。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    host: str = Field(..., description="目标主机 IP")
    delete_backup: bool = Field(default=False, alias="deleteBackup", description="是否同时删除备份文件")


class PortCheckQuery(BaseModel):
    """端口冲突检测请求参数。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    host: str = Field(..., description="目标主机 IP")
    port: int = Field(..., description="待检测端口")
    exclude_config: Optional[str] = Field(default=None, alias="excludeConfig", description="排除的当前配置文件名")

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        return ensure_valid_port(value)
