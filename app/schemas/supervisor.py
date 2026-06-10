"""Supervisor API 请求模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.exceptions import ParamError
from app.core.security import ensure_safe_host, ensure_safe_name, ensure_valid_port


class ServiceCreateRequest(BaseModel):
    """新增服务请求。"""

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


class SupervisorImportRequest(BaseModel):
    """初始化导入请求。"""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., description="目标主机 IP")
    mode: Literal["DRY_RUN", "APPLY"] = Field(..., description="导入模式：DRY_RUN 仅预检，APPLY 正式导入")

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            return ensure_safe_host(value)
        except ParamError as exc:
            raise ValueError(exc.msg) from exc


class ServiceListQuery(BaseModel):
    """服务列表分页查询参数。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    host: str | None = Field(default=None, description="目标主机 IP")
    keyword: str | None = Field(default=None, description="模糊搜索关键字")
    status: str | None = Field(default=None, description="按状态过滤")
    page: int = Field(default=1, ge=1, description="当前页码")
    page_size: int = Field(default=10, alias="pageSize", description="每页条数，只允许 10/20/50")

    @field_validator("page_size")
    @classmethod
    def validate_page_size(cls, value: int) -> int:
        if value not in {10, 20, 50}:
            raise ValueError("pageSize 只允许 10、20 或 50")
        return value

    @field_validator("host")
    @classmethod
    def validate_optional_host(cls, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return ensure_safe_host(value)
        except ParamError as exc:
            raise ValueError(exc.msg) from exc


class ServiceListRecord(BaseModel):
    """服务列表单条记录视图（纯数据库快照，不含实时远端数据）。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    host: str = Field(alias="hostIp")
    job_name: str | None = Field(default=None, alias="jobName")
    module_name: str | None = Field(default=None, alias="moduleName")
    program_name: str = Field(alias="programName")
    config_name: str = Field(alias="configName")
    config_path: str = Field(alias="configPath")
    file_name: str = Field(alias="fileName")
    manage_mode: str = Field(alias="manageMode")
    metadata_complete: bool = Field(alias="metadataComplete")
    parse_warnings: list[str] = Field(default=[], alias="parseWarnings")
    java_path: str | None = Field(default=None, alias="javaPath")
    active: str | None = Field(default=None)
    port: int | None = Field(default=None)
    jar_name: str | None = Field(default=None, alias="jarName")
    xms: str | None = Field(default=None)
    xmx: str | None = Field(default=None)
    user: str | None = Field(default=None)
    status: str = Field(default="UNKNOWN")
    pid: str | None = Field(default=None)
    uptime: str | None = Field(default=None)
    update_time: str | None = Field(default=None, alias="updateTime")

    @classmethod
    def from_record(cls, record) -> "ServiceListRecord":
        return cls(
            id=record.id,
            hostIp=record.host_ip,
            jobName=record.job_name,
            moduleName=record.module_name,
            programName=record.program_name,
            configName=record.config_name,
            configPath=record.config_path,
            fileName=record.file_name,
            manageMode=record.manage_mode,
            metadataComplete=record.metadata_complete,
            parseWarnings=list(record.parse_warnings),
            javaPath=record.java_path,
            active=record.active_profile,
            port=record.port,
            jarName=record.jar_name,
            xms=record.xms,
            xmx=record.xmx,
            user=record.run_user,
            status=record.status,
            pid=record.pid,
            uptime=record.uptime,
            updateTime=record.update_time.strftime("%Y-%m-%d %H:%M:%S") if record.update_time else None,
        )


class PagedServiceResponse(BaseModel):
    """服务列表分页响应。"""

    model_config = ConfigDict(populate_by_name=True)

    records: list[ServiceListRecord]
    page: int
    page_size: int = Field(alias="pageSize")
    total: int
    pages: int


class StatusRefreshResponse(BaseModel):
    """状态刷新响应。"""

    model_config = ConfigDict(populate_by_name=True)

    host: str
    total: int
    updated: int
    missing: int
