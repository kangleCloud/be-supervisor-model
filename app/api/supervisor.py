"""Supervisor 管理 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.config import get_settings
from app.core.response import ok
from app.core.security import verify_jwt_dependency
from app.schemas.supervisor import ServiceCreateRequest
from app.services.auth_service import AuthenticatedUser
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_manager import SupervisorManager
from app.services.supervisor_registry_service import SupervisorRegistryService
from app.services.supervisor_service import SupervisorService
from app.services.template_service import TemplateService


router = APIRouter(
    prefix="/admin/api/supervisor",
    tags=["Supervisor 管理"],
    dependencies=[Depends(verify_jwt_dependency)],
)


def get_manager() -> SupervisorManager:
    """构造业务编排服务。"""
    settings = get_settings()
    host_service = HostService(settings)
    template_service = TemplateService(settings)
    config_file_service = ConfigFileService(settings, host_service, template_service)
    port_check_service = PortCheckService(config_file_service)
    supervisor_service = SupervisorService(host_service)
    registry_service = SupervisorRegistryService(settings)
    return SupervisorManager(
        host_service,
        template_service,
        config_file_service,
        port_check_service,
        supervisor_service,
        registry_service,
    )


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
    description="按主机查询数据库中的纳管服务，并实时补充 supervisorctl 状态与远端文件漂移状态；兼容返回 configName/programName，同时显式返回 configPath/fileName/contentProgramName。",
    response_description="服务列表。",
)
def list_services(host: str = Query(..., description="目标主机 IP"), manager: SupervisorManager = Depends(get_manager)):
    return ok(manager.list_services(host), msg="查询服务列表成功")


@router.get(
    "/services/{program_name}",
    summary="查询 Supervisor 服务详情",
    description="返回数据库中的纳管配置、模板基线或导入快照内容，以及按 configPath 读取到的远端文件状态。",
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
    description="仅允许 local 主机新增 Supervisor 服务：先在本机写入配置并执行 reread/update，成功后再把模板纳管主数据落库。",
    response_description="新增结果。",
)
def create_service(
    payload: ServiceCreateRequest,
    manager: SupervisorManager = Depends(get_manager),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(manager.create_service(payload, current_user), msg="新增服务成功")
