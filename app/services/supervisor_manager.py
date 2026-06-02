"""Supervisor 业务编排服务。"""
from __future__ import annotations

from app.core.exceptions import ConfigAlreadyExistsError
from app.core.security import ensure_safe_program_name
from app.schemas.supervisor import ServiceUpsertRequest
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_service import SupervisorService
from app.services.template_service import TemplateService


class SupervisorManager:
    """聚合 API 所需的业务能力。"""

    def __init__(
        self,
        host_service: HostService,
        template_service: TemplateService,
        config_file_service: ConfigFileService,
        port_check_service: PortCheckService,
        supervisor_service: SupervisorService,
    ):
        self.host_service = host_service
        self.template_service = template_service
        self.config_file_service = config_file_service
        self.port_check_service = port_check_service
        self.supervisor_service = supervisor_service

    def list_hosts(self) -> list[dict[str, object]]:
        """返回允许的主机列表。"""
        return self.host_service.list_hosts()

    def list_services(self, host: str) -> list[dict[str, object]]:
        """列出服务及其状态。"""
        self.host_service.get_host(host)
        status_map = {item.program_name: item.to_dict() for item in self.supervisor_service.status(host)}
        records = self.config_file_service.list_configs(host, include_backups=False)
        result = []
        for record in records:
            result.append(
                {
                    "configName": record.config_name,
                    "configPath": record.path,
                    "programName": record.parsed.program_name,
                    "port": record.parsed.port,
                    "javaPath": record.parsed.java_path,
                    "active": record.parsed.active,
                    "jarName": record.parsed.jar_name,
                    "xms": record.parsed.xms,
                    "xmx": record.parsed.xmx,
                    "options": record.parsed.options,
                    "status": status_map.get(record.parsed.program_name),
                }
            )
        return result

    def get_service_detail(self, host: str, program_name: str) -> dict[str, object]:
        """查看单个服务详情。"""
        record = self.config_file_service.find_by_program_name(host, program_name)
        status_entries = self.supervisor_service.status(host, record.parsed.program_name)
        status = status_entries[0].to_dict() if status_entries else None
        return {
            "configName": record.config_name,
            "configPath": record.path,
            "programName": record.parsed.program_name,
            "content": record.content,
            "parsed": {
                "programName": record.parsed.program_name,
                "port": record.parsed.port,
                "javaPath": record.parsed.java_path,
                "active": record.parsed.active,
                "jarName": record.parsed.jar_name,
                "xms": record.parsed.xms,
                "xmx": record.parsed.xmx,
                "options": record.parsed.options,
            },
            "status": status,
        }

    def create_service(self, payload: ServiceUpsertRequest) -> dict[str, object]:
        """新增 Supervisor 服务。"""
        self.host_service.get_host(payload.host)
        rendered = self.template_service.render(payload)
        self.config_file_service.ensure_not_exists(payload.host, rendered.config_name, rendered.program_name)
        self.port_check_service.ensure_no_conflict(payload.host, payload.port)
        config_path = self.config_file_service.write_config(payload.host, rendered.config_name, rendered.content, rendered.program_name)
        reread_result = self.supervisor_service.reread(payload.host)
        update_result = self.supervisor_service.update(payload.host)
        start_result = self.supervisor_service.start(payload.host, rendered.program_name) if payload.auto_start else None
        return {
            "host": payload.host,
            "programName": rendered.program_name,
            "configName": rendered.config_name,
            "configPath": config_path,
            "reread": reread_result,
            "update": update_result,
            "start": start_result,
        }

    def update_service(self, current_program_name: str, payload: ServiceUpsertRequest) -> dict[str, object]:
        """修改服务并允许自动重命名。"""
        safe_current_program_name = ensure_safe_program_name(current_program_name)
        existing = self.config_file_service.find_by_program_name(payload.host, safe_current_program_name)
        rendered = self.template_service.render(payload)
        self.supervisor_service.stop(payload.host, safe_current_program_name)
        backup_result = self.config_file_service.backup_config(payload.host, existing.config_name, existing.parsed.program_name)
        if rendered.config_name != existing.config_name and self.config_file_service.exists(payload.host, rendered.config_name, rendered.program_name):
            raise ConfigAlreadyExistsError(f"配置文件已存在: {rendered.config_name}")
        self.port_check_service.ensure_no_conflict(payload.host, payload.port, exclude_config=existing.config_name)
        config_path = self.config_file_service.write_config(payload.host, rendered.config_name, rendered.content, rendered.program_name)
        if rendered.config_name != existing.config_name:
            self.config_file_service.delete_config(payload.host, existing.config_name, delete_backup=False, program_name=existing.parsed.program_name)
        reread_result = self.supervisor_service.reread(payload.host)
        update_result = self.supervisor_service.update(payload.host)
        start_result = self.supervisor_service.start(payload.host, rendered.program_name) if payload.auto_start else None
        return {
            "host": payload.host,
            "oldProgramName": safe_current_program_name,
            "newProgramName": rendered.program_name,
            "oldConfigName": existing.config_name,
            "newConfigName": rendered.config_name,
            "configPath": config_path,
            "backup": backup_result,
            "reread": reread_result,
            "update": update_result,
            "start": start_result,
        }

    def delete_service(self, host: str, program_name: str, delete_backup: bool = False) -> dict[str, object]:
        """删除服务配置。"""
        record = self.config_file_service.find_by_program_name(host, program_name)
        self.supervisor_service.stop(host, record.parsed.program_name, allow_not_running=True)
        backup_result = self.config_file_service.backup_config(host, record.config_name, record.parsed.program_name)
        delete_result = self.config_file_service.delete_config(host, record.config_name, delete_backup=delete_backup, program_name=record.parsed.program_name)
        reread_result = self.supervisor_service.reread(host)
        update_result = self.supervisor_service.update(host)
        return {
            "host": host,
            "programName": record.parsed.program_name,
            "configName": record.config_name,
            "backup": backup_result,
            "delete": delete_result,
            "reread": reread_result,
            "update": update_result,
        }

    def backup_service(self, host: str, program_name: str) -> dict[str, object]:
        """备份指定服务。"""
        record = self.config_file_service.find_by_program_name(host, program_name)
        result = self.config_file_service.backup_config(host, record.config_name, record.parsed.program_name)
        return {"host": host, "programName": record.parsed.program_name, **result}

    def restore_service(self, host: str, program_name: str, auto_start: bool = False) -> dict[str, object]:
        """还原指定服务。"""
        record = self.config_file_service.find_by_program_name(host, program_name, include_backups=True)
        restore_config_name = self._restore_target_config_name(record.config_name)
        restore_result = self.config_file_service.restore_config(host, restore_config_name, record.parsed.program_name)
        reread_result = self.supervisor_service.reread(host)
        update_result = self.supervisor_service.update(host)
        start_result = self.supervisor_service.start(host, record.parsed.program_name) if auto_start else None
        return {
            "host": host,
            "programName": record.parsed.program_name,
            "restore": restore_result,
            "reread": reread_result,
            "update": update_result,
            "start": start_result,
        }

    def start_service(self, host: str, program_name: str) -> dict[str, object]:
        """启动服务。"""
        return {"host": host, "programName": program_name, "result": self.supervisor_service.start(host, program_name)}

    def stop_service(self, host: str, program_name: str) -> dict[str, object]:
        """停止服务。"""
        return {"host": host, "programName": program_name, "result": self.supervisor_service.stop(host, program_name, allow_not_running=True)}

    def restart_service(self, host: str, program_name: str) -> dict[str, object]:
        """重启服务。"""
        return {"host": host, "programName": program_name, "result": self.supervisor_service.restart(host, program_name)}

    def reread(self, host: str) -> dict[str, object]:
        """执行 reread。"""
        return {"host": host, "result": self.supervisor_service.reread(host)}

    def update(self, host: str) -> dict[str, object]:
        """执行 update。"""
        return {"host": host, "result": self.supervisor_service.update(host)}

    def status(self, host: str, program_name: str | None = None) -> list[dict[str, object]]:
        """查询 Supervisor 状态。"""
        self.host_service.get_host(host)
        return [item.to_dict() for item in self.supervisor_service.status(host, program_name)]

    def check_port(self, host: str, port: int, exclude_config: str | None = None) -> dict[str, object]:
        """执行端口冲突检测。"""
        self.host_service.get_host(host)
        conflicts = self.port_check_service.find_conflicts(host, port, exclude_config=exclude_config)
        return {"host": host, "port": port, "conflicts": [item.to_dict() for item in conflicts]}

    @staticmethod
    def _restore_target_config_name(file_name: str) -> str:
        if file_name.endswith(".ini"):
            return file_name
        if ".ini.bak" in file_name:
            return f"{file_name.split('.ini.bak', 1)[0]}.ini"
        return file_name
