"""Supervisor 主数据仓储测试。"""
from __future__ import annotations

import pytest

from app.core.exceptions import ConfigAlreadyExistsError, PortConflictError
from app.services.supervisor_registry_service import SupervisorRegistryCreateData, SupervisorRegistryService


def _build_create_data(port: int = 9001, program_name: str = "demo-project_member", config_name: str = "demo-project_member.ini"):
    return SupervisorRegistryCreateData(
        host_ip="127.0.0.1",
        job_name="demo-project",
        module_name="member",
        program_name=program_name,
        config_name=config_name,
        java_path="/usr/local/jdk17/bin/java",
        active_profile="prod",
        port=port,
        jar_name="member.jar",
        xms="128m",
        xmx="128m",
        run_user="root",
    )


def test_registry_create_and_query(settings):
    service = SupervisorRegistryService(settings)
    data = _build_create_data()

    created = service.create(data, operator_id=1, operator_name="ops", remark="测试新增")
    records = service.list_by_host("127.0.0.1")
    fetched = service.get_by_program_name("127.0.0.1", "demo-project_member")

    assert created.id == 1
    assert len(records) == 1
    assert fetched.program_name == "demo-project_member"
    assert fetched.config_name == "demo-project_member.ini"


def test_registry_rejects_duplicate_program_name(settings):
    service = SupervisorRegistryService(settings)
    service.create(_build_create_data(), operator_id=1, operator_name="ops", remark="测试新增")

    with pytest.raises(ConfigAlreadyExistsError):
        service.ensure_can_create(_build_create_data(program_name="demo-project_member", config_name="other.ini", port=9002))


def test_registry_rejects_duplicate_config_name(settings):
    service = SupervisorRegistryService(settings)
    service.create(_build_create_data(), operator_id=1, operator_name="ops", remark="测试新增")

    with pytest.raises(ConfigAlreadyExistsError):
        service.ensure_can_create(_build_create_data(program_name="other_member", config_name="demo-project_member.ini", port=9002))


def test_registry_rejects_duplicate_port(settings):
    service = SupervisorRegistryService(settings)
    service.create(_build_create_data(), operator_id=1, operator_name="ops", remark="测试新增")

    with pytest.raises(PortConflictError):
        service.ensure_can_create(_build_create_data(program_name="other_member", config_name="other_member.ini", port=9001))
