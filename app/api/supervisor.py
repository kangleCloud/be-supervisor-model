"""Supervisor 管理 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.config import get_settings
from app.core.response import ok
from app.core.security import verify_jwt_dependency
from app.schemas.supervisor import (
    PagedServiceResponse,
    ServiceCreateRequest,
    ServiceListQuery,
    StatusRefreshResponse,
    SupervisorImportRequest,
)
from app.services.auth_service import AuthenticatedUser
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_archive_service import SupervisorArchiveService
from app.services.supervisor_import_service import SupervisorImportService
from app.services.supervisor_manager import SupervisorManager
from app.services.supervisor_registry_service import SupervisorRegistryService
from app.services.supervisor_runtime_service import SupervisorRuntimeService
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
    import_service = SupervisorImportService(host_service, config_file_service, template_service, registry_service)
    runtime_service = SupervisorRuntimeService(host_service, registry_service, supervisor_service)
    archive_service = SupervisorArchiveService(host_service, config_file_service, registry_service, supervisor_service)
    return SupervisorManager(
        host_service,
        template_service,
        config_file_service,
        port_check_service,
        supervisor_service,
        registry_service,
        import_service,
        runtime_service,
        archive_service,
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
    summary="分页查询 Supervisor 服务列表",
    description="纯数据库分页查询纳管服务列表，按 update_time DESC, id DESC 排序；支持 host/keyword/status 过滤。不触发任何远端命令。",
    response_description="分页服务列表。",
)
def list_services(
    query: ServiceListQuery = Depends(),
    manager: SupervisorManager = Depends(get_manager),
):
    result = manager.list_services_page(query)
    return ok(result, msg="查询服务列表成功")


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
    "/imports",
    summary="初始化导入 Supervisor 配置",
    description="固定递归扫描目标主机 /etc/supervisord.d 下的 *.ini，DRY_RUN 仅返回逐文件预检结果，APPLY 才会把只读快照写入数据库。",
    response_description="导入汇总与逐文件结果。",
)
def import_services(
    payload: SupervisorImportRequest,
    manager: SupervisorManager = Depends(get_manager),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(manager.import_services(payload, current_user), msg="执行初始化导入成功")


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


@router.post(
    "/services/status/refresh",
    summary="刷新服务状态快照",
    description="对指定主机执行一次 supervisorctl status，批量刷新数据库中的 status/pid/uptime/status_sync_time 快照。",
    response_description="刷新汇总。",
)
def refresh_service_status(
    host: str = Query(..., description="目标主机 IP"),
    manager: SupervisorManager = Depends(get_manager),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(manager.refresh_status(host), msg="刷新服务状态成功")


@router.post(
    "/services/{program_name}/start",
    summary="启动 Supervisor 服务",
    description="对指定主机上的纳管服务执行 supervisorctl start，并在成功后刷新数据库状态快照。",
    response_description="运行操作结果。",
)
def start_service(
    program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    manager: SupervisorManager = Depends(get_manager),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(manager.start_service(host, program_name, current_user), msg="启动服务成功")


@router.post(
    "/services/{program_name}/stop",
    summary="停止 Supervisor 服务",
    description="对指定主机上的纳管服务执行 supervisorctl stop，并在成功后刷新数据库状态快照。",
    response_description="运行操作结果。",
)
def stop_service(
    program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    manager: SupervisorManager = Depends(get_manager),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(manager.stop_service(host, program_name, current_user), msg="停止服务成功")


@router.post(
    "/services/{program_name}/restart",
    summary="重启 Supervisor 服务",
    description="对指定主机上的纳管服务执行 supervisorctl restart，并在成功后刷新数据库状态快照。",
    response_description="运行操作结果。",
)
def restart_service(
    program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    manager: SupervisorManager = Depends(get_manager),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(manager.restart_service(host, program_name, current_user), msg="重启服务成功")


@router.post(
    "/services/{program_name}/archive",
    summary="归档 Supervisor 服务",
    description="先停止服务，再备份并删除远端配置文件，随后执行 reread/update，最后把数据库记录标记为已归档。",
    response_description="归档结果。",
)
def archive_service(
    program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    manager: SupervisorManager = Depends(get_manager),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(manager.archive_service(host, program_name, current_user), msg="归档服务成功")


@router.post(
    "/services/{program_name}/restore",
    summary="还原 Supervisor 服务",
    description="从归档备份恢复远端配置文件，执行 reread/update，并同步数据库状态；不会自动启动服务。",
    response_description="还原结果。",
)
def restore_service(
    program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    manager: SupervisorManager = Depends(get_manager),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(manager.restore_service(host, program_name, current_user), msg="还原服务成功")
