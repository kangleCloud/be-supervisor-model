"""Supervisor 管理 API。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.core.async_utils import run_blocking
from app.core.config import get_settings
from app.core.response import ok
from app.core.security import verify_jwt_dependency
from app.schemas.supervisor import (
    PagedServiceResponse,
    ServiceCreateRequest,
    ServiceListQuery,
    ServiceListRecord,
    ServiceUpdateRequest,
    SupervisorImportStagingQuery,
    SupervisorImportStagingResponse,
    StatusRefreshResponse,
    SupervisorImportRequest,
    SupervisorOverviewResponse,
)
from app.services.auth_service import AuthenticatedUser
from app.services.config_file_service import ConfigFileService
from app.services.host_service import HostService
from app.services.port_check_service import PortCheckService
from app.services.supervisor_archive_service import SupervisorArchiveService
from app.services.supervisor_detail_service import SupervisorDetailService
from app.services.supervisor_import_service import SupervisorImportService
from app.services.supervisor_mutation_service import SupervisorMutationService
from app.services.supervisor_overview_service import SupervisorOverviewService
from app.services.supervisor_registry_service import ImportStagingService, SupervisorRegistryService
from app.services.supervisor_runtime_service import SupervisorRuntimeService
from app.services.supervisor_service import SupervisorService
from app.services.supervisor_sync_service import SupervisorSyncService
from app.services.template_service import TemplateService


router = APIRouter(
    prefix="/admin/api/supervisor",
    tags=["Supervisor 管理"],
    dependencies=[Depends(verify_jwt_dependency)],
)

# ---- 共享依赖 ----

_settings = get_settings()
_host_service = HostService(_settings)
_template_service = TemplateService(_settings)
_registry_service = SupervisorRegistryService(_settings)
_staging_service = ImportStagingService(_settings)
_config_file_service = ConfigFileService(_settings, _host_service, _template_service)
_port_check_service = PortCheckService(_config_file_service, _host_service)
_supervisor_service = SupervisorService(_host_service)
_detail_service = SupervisorDetailService(_host_service, _registry_service)
_import_service = SupervisorImportService(
    _host_service,
    _config_file_service,
    _template_service,
    _registry_service,
    _staging_service,
)
_mutation_service = SupervisorMutationService(
    _host_service, _template_service, _config_file_service,
    _port_check_service, _supervisor_service, _registry_service,
)
_runtime_service = SupervisorRuntimeService(_host_service, _registry_service, _supervisor_service)
_archive_service = SupervisorArchiveService(_host_service, _config_file_service, _registry_service, _supervisor_service)
_sync_service = SupervisorSyncService(
    _host_service, _config_file_service, _registry_service, _supervisor_service, _template_service,
)
_overview_service = SupervisorOverviewService(_settings, _host_service)

LOGGER = logging.getLogger(__name__)


# ---- 主机查询 ----

@router.get(
    "/hosts",
    summary="查询目标主机列表",
    description="返回配置中的主机白名单及执行器类型。",
    response_description="主机列表。",
)
async def list_hosts():
    return ok(await run_blocking(_host_service.list_hosts), msg="查询主机列表成功")


@router.get(
    "/overview",
    summary="查询主机实时概况",
    description="实时采集目标主机 CPU、内存和 Supervisor 基础检查结果，不落库。",
    response_description="主机实时概况。",
)
async def get_host_overview(host: str = Query(..., description="目标主机 IP")):
    result = SupervisorOverviewResponse(**await run_blocking(_overview_service.get_overview, host))
    payload = ok(result.model_dump(by_alias=True), msg="查询主机概况成功")
    payload.headers["Cache-Control"] = "no-store"
    return payload


# ---- 服务列表 ----

@router.get(
    "/services",
    summary="分页查询 Supervisor 服务列表",
    description="纯数据库分页查询纳管服务列表，按 update_time DESC, id DESC 排序。",
    response_description="分页服务列表。",
)
async def list_services(query: ServiceListQuery = Depends()):
    # 列表接口固定只查数据库快照，不允许在这里隐式触发任何远端命令。
    LOGGER.info(
        "查询服务列表：目标主机=%s，关键字=%s，状态=%s，归档筛选=%s，当前页=%s，每页条数=%s",
        query.host, query.keyword, query.status, query.archived, query.page, query.page_size,
    )
    records, total, pages = await _registry_service.search_page(
        host=query.host, keyword=query.keyword, status=query.status,
        archived=query.archived, page=query.page, page_size=query.page_size,
    )
    result = PagedServiceResponse(
        records=[ServiceListRecord.from_record(r) for r in records],
        page=query.page, pageSize=query.page_size, total=total, pages=pages,
    )
    return ok(result.model_dump(by_alias=True), msg="查询服务列表成功")


# ---- 服务详情与同步 ----

@router.get(
    "/services/{content_program_name}",
    summary="查询 Supervisor 服务详情",
    description="只返回数据库中的单服务详情快照。",
    response_description="服务详情。",
)
async def get_service_detail(content_program_name: str, host: str = Query(..., description="目标主机 IP")):
    return ok(await _detail_service.get_service_detail(host, content_program_name), msg="查询服务详情成功")


@router.post(
    "/services/{content_program_name}/sync",
    summary="同步单个 Supervisor 服务详情快照",
    description="显式读取远端 supervisorctl status 和 .ini，并回写数据库详情快照。",
    response_description="同步结果。",
)
async def sync_service_detail(content_program_name: str, host: str = Query(..., description="目标主机 IP")):
    return ok(await _sync_service.sync_service(host, content_program_name), msg="同步服务详情成功")


# ---- 增改删 ----

@router.post(
    "/services",
    summary="新增 Supervisor 服务",
    description="写入目标主机配置并执行 reread/update，成功后主数据落库。",
    response_description="新增结果。",
)
async def create_service(
    payload: ServiceCreateRequest,
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(await _mutation_service.create_service(payload, current_user), msg="新增服务成功")


@router.put(
    "/services/{content_program_name}",
    summary="修改 Supervisor 服务",
    description="支持修改已纳管服务；允许改字段、改名、改端口。",
    response_description="修改结果。",
)
async def update_service(
    content_program_name: str,
    payload: ServiceUpdateRequest,
    host: str = Query(..., description="目标主机 IP"),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(await _mutation_service.update_service(host, content_program_name, payload, current_user), msg="修改服务成功")


@router.delete(
    "/services/{content_program_name}",
    summary="删除 Supervisor 服务",
    description="停止服务，删除远端配置，移除数据库记录。",
    response_description="删除结果。",
)
async def delete_service(
    content_program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(await _mutation_service.delete_service(host, content_program_name, current_user), msg="删除服务成功")


# ---- 状态刷新 ----

@router.post(
    "/services/status/refresh",
    summary="刷新服务状态快照",
    description="对指定主机执行 supervisorctl status，批量刷新数据库状态。",
    response_description="刷新汇总。",
)
async def refresh_service_status(host: str = Query(..., description="目标主机 IP")):
    # 状态刷新属于显式远端读取入口，只有当前端主动点击刷新时才会执行 supervisorctl status。
    LOGGER.info("刷新服务状态：目标主机=%s", host)
    host_config = await run_blocking(_host_service.get_host, host)
    status_entries = await run_blocking(_supervisor_service.status, host)
    status_tuples = [(entry.program_name, entry.state, entry.pid, entry.uptime) for entry in status_entries]
    updated, missing = await _registry_service.batch_update_status(host, status_tuples)
    LOGGER.info("刷新服务状态成功：目标主机=%s，更新条数=%s，未匹配条数=%s", host, updated, missing)
    return ok(
        StatusRefreshResponse(host=host_config.ip, total=len(status_entries), updated=updated, missing=missing).model_dump(by_alias=True),
        msg="刷新服务状态成功",
    )


# ---- 初始化导入 ----

@router.get(
    "/imports/staging",
    summary="查询初始化导入暂存结果",
    description="按目标主机恢复当前登录用户最近一次 PRECHECK 暂存结果，供页面刷新后继续展示。",
    response_description="最近一批初始化导入暂存结果。",
)
async def get_import_staging(
    query: SupervisorImportStagingQuery = Depends(),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    result = await _import_service.load_staging(host=query.host, operator_id=current_user.user_id)
    response = SupervisorImportStagingResponse(**result)
    return ok(response.model_dump(by_alias=True), msg="查询初始化导入暂存成功")

@router.post(
    "/imports",
    summary="初始化导入 Supervisor 配置",
    description="扫描目标主机 /etc/supervisord.d 下 *.ini，PRECHECK 写入暂存表返回 batchId，COMMIT 原子提交到正式表。",
    response_description="导入汇总与逐文件结果。",
)
async def import_services(
    payload: SupervisorImportRequest,
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    report = await _import_service.execute(
        host=payload.host, mode=payload.mode,
        operator_id=current_user.user_id, operator_name=current_user.username,
        batch_id=payload.batch_id,
        recursive=True,
    )
    return ok(report.to_dict(), msg="执行初始化导入成功")


# ---- 运行操作 ----

@router.post(
    "/services/{content_program_name}/start",
    summary="启动 Supervisor 服务",
    response_description="运行操作结果。",
)
async def start_service(content_program_name: str, host: str = Query(..., description="目标主机 IP")):
    return ok(await _runtime_service.start_service(host, content_program_name), msg="启动服务成功")


@router.post(
    "/services/{content_program_name}/stop",
    summary="停止 Supervisor 服务",
    response_description="运行操作结果。",
)
async def stop_service(content_program_name: str, host: str = Query(..., description="目标主机 IP")):
    return ok(await _runtime_service.stop_service(host, content_program_name), msg="停止服务成功")


@router.post(
    "/services/{content_program_name}/restart",
    summary="重启 Supervisor 服务",
    response_description="运行操作结果。",
)
async def restart_service(content_program_name: str, host: str = Query(..., description="目标主机 IP")):
    return ok(await _runtime_service.restart_service(host, content_program_name), msg="重启服务成功")


# ---- 归档 / 还原 ----

@router.post(
    "/services/{content_program_name}/archive",
    summary="归档 Supervisor 服务",
    description="停止服务 → 备份配置 → 删除配置 → reread/update → 写库标记。",
    response_description="归档结果。",
)
async def archive_service(
    content_program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(
        await _archive_service.archive_service(host, content_program_name, operator_id=current_user.user_id, operator_name=current_user.username),
        msg="归档服务成功",
    )


@router.post(
    "/services/{content_program_name}/restore",
    summary="还原 Supervisor 服务",
    description="从备份恢复配置文件 → reread/update → 同步状态，不自动启动。",
    response_description="还原结果。",
)
async def restore_service(
    content_program_name: str,
    host: str = Query(..., description="目标主机 IP"),
    current_user: AuthenticatedUser = Depends(verify_jwt_dependency),
):
    return ok(
        await _archive_service.restore_service(host, content_program_name, operator_id=current_user.user_id, operator_name=current_user.username),
        msg="还原服务成功",
    )
