"""Supervisor 主数据落库服务。"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.database import get_connection
from app.core.exceptions import ConfigAlreadyExistsError, ConfigNotFoundError, PortConflictError
from app.core.security import ensure_safe_host, ensure_safe_program_name, ensure_valid_port


@dataclass(frozen=True)
class SupervisorRegistryCreateData:
    """创建 Supervisor 主数据所需字段。"""

    host_ip: str
    job_name: str
    module_name: str
    program_name: str
    config_name: str
    java_path: str
    active_profile: str
    port: int
    jar_name: str
    xms: str
    xmx: str
    run_user: str


@dataclass(frozen=True)
class SupervisorRegistryRecord:
    """Supervisor 主数据记录。"""

    id: int
    host_ip: str
    job_name: str
    module_name: str
    program_name: str
    config_name: str
    java_path: str
    active_profile: str
    port: int
    jar_name: str
    xms: str
    xmx: str
    run_user: str


class SupervisorRegistryService:
    """负责 Supervisor 配置主数据的查询与新增。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def list_by_host(self, host: str) -> list[SupervisorRegistryRecord]:
        """按主机查询已纳管的 Supervisor 服务。"""
        safe_host = ensure_safe_host(host)
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, host_ip, job_name, module_name, program_name, config_name,
                           java_path, active_profile, port, jar_name, xms, xmx, run_user
                    FROM sys_supervisor_service
                    WHERE host_ip = %s
                    ORDER BY id ASC
                    """,
                    (safe_host,),
                )
                rows = cursor.fetchall()
        return [self._build_record(row) for row in rows]

    def get_by_program_name(self, host: str, program_name: str) -> SupervisorRegistryRecord:
        """按主机和 programName 查询单条记录。"""
        safe_host = ensure_safe_host(host)
        safe_program_name = ensure_safe_program_name(program_name)
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, host_ip, job_name, module_name, program_name, config_name,
                           java_path, active_profile, port, jar_name, xms, xmx, run_user
                    FROM sys_supervisor_service
                    WHERE host_ip = %s AND program_name = %s
                    LIMIT 1
                    """,
                    (safe_host, safe_program_name),
                )
                row = cursor.fetchone()
        if row is None:
            raise ConfigNotFoundError(f"未找到服务 {safe_program_name}")
        return self._build_record(row)

    def ensure_can_create(self, data: SupervisorRegistryCreateData) -> None:
        """校验同主机下 programName、configName、port 不能重复。"""
        ensure_valid_port(data.port)
        for record in self.list_by_host(data.host_ip):
            if record.program_name == data.program_name:
                raise ConfigAlreadyExistsError(f"服务已存在: {record.program_name}")
            if record.config_name == data.config_name:
                raise ConfigAlreadyExistsError(f"配置文件已存在: {record.config_name}")
            if record.port == data.port:
                raise PortConflictError(
                    "端口冲突",
                    [
                        {
                            "host": record.host_ip,
                            "programName": record.program_name,
                            "configName": record.config_name,
                            "port": record.port,
                        }
                    ],
                )

    def create(
        self,
        data: SupervisorRegistryCreateData,
        *,
        operator_id: int,
        operator_name: str,
        remark: str,
    ) -> SupervisorRegistryRecord:
        """新增一条 Supervisor 主数据记录。"""
        with get_connection(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO sys_supervisor_service(
                        host_ip, job_name, module_name, program_name, config_name,
                        java_path, active_profile, port, jar_name, xms, xmx, run_user,
                        create_by_id, create_by, update_by_id, update_by, remark
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        data.host_ip,
                        data.job_name,
                        data.module_name,
                        data.program_name,
                        data.config_name,
                        data.java_path,
                        data.active_profile,
                        data.port,
                        data.jar_name,
                        data.xms,
                        data.xmx,
                        data.run_user,
                        operator_id,
                        operator_name,
                        operator_id,
                        operator_name,
                        remark,
                    ),
                )
                record_id = int(cursor.lastrowid)
            connection.commit()
        return SupervisorRegistryRecord(
            id=record_id,
            host_ip=data.host_ip,
            job_name=data.job_name,
            module_name=data.module_name,
            program_name=data.program_name,
            config_name=data.config_name,
            java_path=data.java_path,
            active_profile=data.active_profile,
            port=data.port,
            jar_name=data.jar_name,
            xms=data.xms,
            xmx=data.xmx,
            run_user=data.run_user,
        )

    @staticmethod
    def _build_record(row: dict[str, object]) -> SupervisorRegistryRecord:
        return SupervisorRegistryRecord(
            id=int(row["id"]),
            host_ip=str(row["host_ip"]),
            job_name=str(row["job_name"]),
            module_name=str(row["module_name"]),
            program_name=str(row["program_name"]),
            config_name=str(row["config_name"]),
            java_path=str(row["java_path"]),
            active_profile=str(row["active_profile"]),
            port=int(row["port"]),
            jar_name=str(row["jar_name"]),
            xms=str(row["xms"]),
            xmx=str(row["xmx"]),
            run_user=str(row["run_user"]),
        )
