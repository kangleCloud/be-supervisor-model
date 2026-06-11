"""Supervisor API 请求模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.exceptions import ParamError
from app.core.security import ensure_safe_host, ensure_safe_name, ensure_valid_port


def _format_datetime_text(value: object) -> str | None:
    """兼容真实 MySQL datetime 和测试夹具中的字符串时间。"""
    if value in (None, ""):
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


class _ServiceMutationRequestFields(BaseModel):
    """Supervisor 增改共用字段。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    job_name: str = Field(..., alias="jobName", description="业务作业名称")
    module_name: str = Field(..., alias="moduleName", description="模块名称")
    java_path: str = Field(..., alias="javaPath", description="Java 可执行文件绝对路径")
    active: str = Field(..., description="Spring profile 环境")
    port: int = Field(..., description="服务监听端口")
    jar_name: str = Field(default="", alias="jarName", description="Jar 包文件名，默认使用 moduleName.jar")
    file_name: str = Field(default="", alias="fileName", description="配置文件名，可为空")
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


class ServiceCreateRequest(_ServiceMutationRequestFields):
    """新增服务请求。"""

    host: str = Field(..., description="目标主机 IP")

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            return ensure_safe_host(value)
        except ParamError as exc:
            raise ValueError(exc.msg) from exc


class ServiceUpdateRequest(_ServiceMutationRequestFields):
    """修改服务请求。"""


class SupervisorImportRequest(BaseModel):
    """初始化导入请求（两阶段：PRECHECK 写暂存表，COMMIT 原子提交）。"""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., description="目标主机 IP")
    mode: Literal["PRECHECK", "COMMIT"] = Field(..., description="导入模式：PRECHECK 预检写入暂存表，COMMIT 原子提交")
    batch_id: str | None = Field(default=None, alias="batchId", description="COMMIT 模式时必传的批次标识")

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            return ensure_safe_host(value)
        except ParamError as exc:
            raise ValueError(exc.msg) from exc

    @model_validator(mode="after")
    def validate_batch_id_rules(self) -> "SupervisorImportRequest":
        if self.mode == "PRECHECK" and self.batch_id:
            raise ValueError("PRECHECK 模式不能传 batchId")
        if self.mode == "COMMIT" and not (self.batch_id or "").strip():
            raise ValueError("COMMIT 模式必须传 batchId")
        return self


class ServiceListQuery(BaseModel):
    """服务列表分页查询参数。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    host: str | None = Field(default=None, description="目标主机 IP")
    keyword: str | None = Field(default=None, description="模糊搜索关键字")
    status: str | None = Field(default=None, description="按状态过滤")
    archived: str = Field(default="false", description="归档筛选：false=未归档，true=已归档，all=全部")
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

    @field_validator("archived")
    @classmethod
    def validate_archived(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"false", "true", "all"}:
            raise ValueError("archived 只允许 false、true 或 all")
        return normalized


class ServiceListRecord(BaseModel):
    """服务列表单条记录视图。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    host: str = Field(alias="hostIp")
    job_name: str | None = Field(default=None, alias="jobName")
    module_name: str | None = Field(default=None, alias="moduleName")
    content_program_name: str = Field(alias="contentProgramName")
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
    is_archived: bool = Field(default=False, alias="isArchived")
    archived_at: str | None = Field(default=None, alias="archivedAt")
    restored_at: str | None = Field(default=None, alias="restoredAt")
    update_time: str | None = Field(default=None, alias="updateTime")

    @classmethod
    def from_record(cls, record) -> "ServiceListRecord":
        return cls(
            id=record.id,
            hostIp=record.host_ip,
            jobName=record.job_name,
            moduleName=record.module_name,
            contentProgramName=record.content_program_name,
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
            isArchived=record.is_archived,
            archivedAt=_format_datetime_text(record.archived_at),
            restoredAt=_format_datetime_text(record.restored_at),
            updateTime=_format_datetime_text(record.update_time),
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


class ServiceDetailResponse(BaseModel):
    """服务详情响应。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    host: str
    host_name: str = Field(alias="hostName")
    content_program_name: str = Field(alias="contentProgramName")
    config_path: str = Field(alias="configPath")
    file_name: str = Field(alias="fileName")
    job_name: str | None = Field(default=None, alias="jobName")
    module_name: str | None = Field(default=None, alias="moduleName")
    java_path: str | None = Field(default=None, alias="javaPath")
    active: str | None = Field(default=None)
    port: int | None = Field(default=None)
    jar_name: str | None = Field(default=None, alias="jarName")
    xms: str | None = Field(default=None)
    xmx: str | None = Field(default=None)
    user: str | None = Field(default=None)
    command: str | None = Field(default=None)
    directory: str | None = Field(default=None)
    stdout_logfile: str | None = Field(default=None, alias="stdoutLogfile")
    status: str = Field(default="UNKNOWN")
    pid: str | None = Field(default=None)
    uptime: str | None = Field(default=None)
    has_backup: bool = Field(default=False, alias="hasBackup")
    config_content: str | None = Field(default=None, alias="configContent")
    backup_config_content: str | None = Field(default=None, alias="backupConfigContent")
    is_archived: bool = Field(default=False, alias="isArchived")
    archived_at: str | None = Field(default=None, alias="archivedAt")
    restored_at: str | None = Field(default=None, alias="restoredAt")
    last_sync_at: str | None = Field(default=None, alias="lastSyncAt")
    sync_status: str = Field(default="UNKNOWN", alias="syncStatus")
    sync_error: str | None = Field(default=None, alias="syncError")
    updated_at: str | None = Field(default=None, alias="updatedAt")

    @classmethod
    def from_record(cls, record, *, host_name: str) -> "ServiceDetailResponse":
        return cls(
            id=record.id,
            host=record.host_ip,
            hostName=host_name,
            contentProgramName=record.content_program_name,
            configPath=record.config_path,
            fileName=record.file_name,
            jobName=record.job_name,
            moduleName=record.module_name,
            javaPath=record.java_path,
            active=record.active_profile,
            port=record.port,
            jarName=record.jar_name,
            xms=record.xms,
            xmx=record.xmx,
            user=record.run_user,
            command=record.command,
            directory=record.directory,
            stdoutLogfile=record.stdout_logfile,
            status=record.status,
            pid=record.pid,
            uptime=record.uptime,
            hasBackup=record.has_backup,
            configContent=record.config_content,
            backupConfigContent=record.backup_config_content,
            isArchived=record.is_archived,
            archivedAt=_format_datetime_text(record.archived_at),
            restoredAt=_format_datetime_text(record.restored_at),
            lastSyncAt=_format_datetime_text(record.last_sync_at),
            syncStatus=record.sync_status,
            syncError=record.sync_error,
            updatedAt=_format_datetime_text(record.update_time),
        )


class ServiceSyncResponse(BaseModel):
    """单服务详情同步响应。"""

    model_config = ConfigDict(populate_by_name=True)

    host: str
    content_program_name: str = Field(alias="contentProgramName")
    status: str
    pid: str | None = Field(default=None)
    uptime: str | None = Field(default=None)
    synced_fields: list[str] = Field(default_factory=list, alias="syncedFields")
    warnings: list[str] = Field(default_factory=list)
    last_sync_at: str = Field(alias="lastSyncAt")
    sync_status: str = Field(alias="syncStatus")
    sync_error: str | None = Field(default=None, alias="syncError")
    command_results: dict[str, object] = Field(alias="commandResults")


class ServiceUpdateResponse(BaseModel):
    """服务修改响应。"""

    model_config = ConfigDict(populate_by_name=True)

    host: str
    previous_content_program_name: str = Field(alias="previousContentProgramName")
    content_program_name: str = Field(alias="contentProgramName")
    config_path: str = Field(alias="configPath")
    file_name: str = Field(alias="fileName")
    manage_mode: str = Field(alias="manageMode")
    command_results: dict[str, object] = Field(alias="commandResults")


class ServiceDeleteResponse(BaseModel):
    """服务删除响应。"""

    model_config = ConfigDict(populate_by_name=True)

    host: str
    content_program_name: str = Field(alias="contentProgramName")
    deleted_config_path: str = Field(alias="deletedConfigPath")
    backup_path: str | None = Field(default=None, alias="backupPath")
    command_results: dict[str, object] = Field(alias="commandResults")


class RuntimeActionResponse(BaseModel):
    """运行操作响应。"""

    model_config = ConfigDict(populate_by_name=True)

    host: str
    content_program_name: str = Field(alias="contentProgramName")
    action: str
    status: str
    command_result: dict[str, object] = Field(alias="commandResult")


class ArchiveActionResponse(BaseModel):
    """归档/还原响应。"""

    model_config = ConfigDict(populate_by_name=True)

    host: str
    content_program_name: str = Field(alias="contentProgramName")
    is_archived: bool = Field(alias="isArchived")
    archived_at: str | None = Field(default=None, alias="archivedAt")
    restored_at: str | None = Field(default=None, alias="restoredAt")
    status: str
    command_result: dict[str, object] = Field(alias="commandResult")
    file_result: dict[str, object] = Field(alias="fileResult")
