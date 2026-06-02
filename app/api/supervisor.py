"""Supervisor 管理 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.config import get_settings
from app.core.security import verify_api_token_dependency
from app.core.response import ok
from app.schemas.supervisor import DeleteServiceQuery, HostRequest, PortCheckQuery, ServiceUpsertRequest
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_manager import SupervisorManager
from app.services.supervisor_service import SupervisorService
from app.services.template_service import TemplateService


router = APIRouter(
    prefix="/api/supervisor",
    tags=["Supervisor 管理"],
    dependencies=[Depends(verify_api_token_dependency)],
)


def get_manager() -> SupervisorManager:
    """构造业务编排服务。"""
    settings = get_settings()
    host_service = HostService(settings)
    template_service = TemplateService(settings)
    config_file_service = ConfigFileService(settings, host_service, template_service)
    port_check_service = PortCheckService(config_file_service)
    supervisor_service = SupervisorService(host_service)
    return SupervisorManager(host_service, template_service, config_file_service, port_check_service, supervisor_service)


@router.get(
    "/hosts",
    summary="查询目标主机列表",
    description="返回配置中的主机白名单及执行器类型。",
    response_description="主机列表。",
)
def list_hosts(manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.list_hosts(), msg="查询主机列表成功")


@router.get(
    "/services",
    summary="查询 Supervisor 服务列表",
    description="返回目标主机当前配置文件对应的服务列表，并合并 supervisorctl status 结果。",
    response_description="服务列表。",
)
def list_services(host: str = Query(..., description="目标主机 IP"), manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.list_services(host), msg="查询服务列表成功")


@router.get(
    "/services/{program_name}",
    summary="查询 Supervisor 服务详情",
    description="返回指定 programName 的原始配置文本、结构化字段和当前状态。",
    response_description="服务详情。",
)
def get_service_detail(
    program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    manager: SupervisorManager = Depends(get_manager),
):
    return ok(manager.get_service_detail(host, program_name), msg="查询服务详情成功")


@router.post(
    "/services",
    summary="新增 Supervisor 服务",
    description="根据表单变量渲染 Supervisor 模板，写入配置文件，并执行 reread/update。",
    response_description="新增结果。",
)
def create_service(payload: ServiceUpsertRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.create_service(payload), msg="新增服务成功")


@router.put(
    "/services/{program_name}",
    summary="修改 Supervisor 服务",
    description="先停止旧服务并备份旧配置，再写入新配置，支持自动重命名 programName 与 configName。",
    response_description="修改结果。",
)
def update_service(program_name: str, payload: ServiceUpsertRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.update_service(program_name, payload), msg="修改服务成功")


@router.delete(
    "/services/{program_name}",
    summary="删除 Supervisor 服务",
    description="删除前会自动备份配置，并执行 reread/update。",
    response_description="删除结果。",
)
def delete_service(
    program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    delete_backup: bool = Query(default=False, alias="deleteBackup", description="是否同时删除备份文件"),
    manager: SupervisorManager = Depends(get_manager),
):
    return ok(manager.delete_service(host, program_name, delete_backup=delete_backup), msg="删除服务成功")


@router.post(
    "/services/{program_name}/start",
    summary="启动服务",
    description="执行 supervisorctl start。",
    response_description="启动结果。",
)
def start_service(program_name: str, payload: HostRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.start_service(payload.host, program_name), msg="启动服务成功")


@router.post(
    "/services/{program_name}/stop",
    summary="停止服务",
    description="执行 supervisorctl stop。",
    response_description="停止结果。",
)
def stop_service(program_name: str, payload: HostRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.stop_service(payload.host, program_name), msg="停止服务成功")


@router.post(
    "/services/{program_name}/restart",
    summary="重启服务",
    description="执行 supervisorctl restart。",
    response_description="重启结果。",
)
def restart_service(program_name: str, payload: HostRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.restart_service(payload.host, program_name), msg="重启服务成功")


@router.post(
    "/services/{program_name}/backup",
    summary="备份配置",
    description="把 *.ini 备份为 *.ini.bak，若旧备份存在会先归档。",
    response_description="备份结果。",
)
def backup_service(program_name: str, payload: HostRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.backup_service(payload.host, program_name), msg="备份配置成功")


@router.post(
    "/services/{program_name}/restore",
    summary="还原配置",
    description="把 *.ini.bak 还原为 *.ini，并执行 reread/update。",
    response_description="还原结果。",
)
def restore_service(program_name: str, payload: HostRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.restore_service(payload.host, program_name), msg="还原配置成功")


@router.get(
    "/ports/check",
    summary="检测端口冲突",
    description="扫描 /etc/supervisord.d 下的 *.ini、*.ini.bak 与 *.ini.bak.* 文件，返回端口冲突结果。",
    response_description="端口冲突结果。",
)
def check_ports(
    host: str = Query(..., description="目标主机 IP"),
    port: int = Query(..., description="待检测端口"),
    exclude_config: str | None = Query(default=None, alias="excludeConfig", description="排除的当前配置文件名"),
    manager: SupervisorManager = Depends(get_manager),
):
    validated = PortCheckQuery(host=host, port=port, excludeConfig=exclude_config)
    return ok(manager.check_port(validated.host, validated.port, exclude_config=validated.exclude_config), msg="端口检测完成")


@router.post(
    "/reread",
    summary="执行 supervisorctl reread",
    description="在指定主机上执行 supervisorctl reread。",
    response_description="执行结果。",
)
def reread(payload: HostRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.reread(payload.host), msg="执行 reread 成功")


@router.post(
    "/update",
    summary="执行 supervisorctl update",
    description="在指定主机上执行 supervisorctl update。",
    response_description="执行结果。",
)
def update(payload: HostRequest, manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.update(payload.host), msg="执行 update 成功")


@router.get(
    "/status",
    summary="查询 supervisorctl status",
    description="返回目标主机全部服务或指定服务的 Supervisor 状态。",
    response_description="状态结果。",
)
def status(
    host: str = Query(..., description="目标主机 IP"),
    program_name: str | None = Query(default=None, alias="programName", description="可选，指定服务名"),
    manager: SupervisorManager = Depends(get_manager),
):
    return ok(manager.status(host, program_name), msg="查询状态成功")
