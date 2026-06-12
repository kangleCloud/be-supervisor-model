"""Supervisor 主数据仓储测试。"""
from __future__ import annotations

import pytest

from app.core.exceptions import ConfigAlreadyExistsError, PortConflictError
from app.services.supervisor_registry_service import (
    MANAGE_MODE_IMPORTED_READONLY,
    MANAGE_MODE_TEMPLATE_MANAGED,
    SupervisorRegistryCreateData,
    SupervisorRegistryService,
)


def _build_create_data(
    *,
    port: int = 9001,
    job_name: str = "demo-project",
    module_name: str = "member",
    content_program_name: str = "demo-project_member",
    file_name: str = "demo-project_member.ini",
    config_path: str = "demo-project_member.ini",
    manage_mode: str = MANAGE_MODE_TEMPLATE_MANAGED,
):
    return SupervisorRegistryCreateData(
        host_ip="127.0.0.1",
        job_name=job_name,
        module_name=module_name,
        config_path=config_path,
        file_name=file_name,
        content_program_name=content_program_name,
        manage_mode=manage_mode,
        baseline_content=f"[program:{content_program_name}]\n",
        metadata_complete=True,
        parse_warnings=(),
        java_path="/usr/local/jdk17/bin/java",
        active_profile="prod",
        port=port,
        jar_name="member.jar",
        xms="128m",
        xmx="128m",
        run_user="root",
    )


def test_registry_create_and_query(settings, run_db):
    async def scenario():
        service = SupervisorRegistryService(settings)
        data = _build_create_data()

        created = await service.create(data, operator_id=1, operator_name="ops", remark="测试新增")
        fetched = await service.get_by_content_program_name_optional("127.0.0.1", "demo-project_member")

        assert created.id >= 1
        assert fetched is not None
        assert fetched.content_program_name == "demo-project_member"
        assert fetched.file_name == "demo-project_member.ini"
        assert fetched.config_path == "demo-project_member.ini"
        assert fetched.manage_mode == MANAGE_MODE_TEMPLATE_MANAGED

    run_db(scenario)


def test_registry_rejects_duplicate_content_program_name(settings, run_db):
    async def scenario():
        service = SupervisorRegistryService(settings)
        await service.create(_build_create_data(), operator_id=1, operator_name="ops", remark="测试新增")

        with pytest.raises(ConfigAlreadyExistsError):
            await service.ensure_can_create(
                _build_create_data(
                    content_program_name="demo-project_member",
                    file_name="other.ini",
                    config_path="other.ini",
                    port=9002,
                )
            )

    run_db(scenario)


def test_registry_rejects_duplicate_config_path(settings, run_db):
    async def scenario():
        service = SupervisorRegistryService(settings)
        await service.create(_build_create_data(), operator_id=1, operator_name="ops", remark="测试新增")

        with pytest.raises(ConfigAlreadyExistsError):
            await service.ensure_can_create(
                _build_create_data(
                    content_program_name="other_member",
                    file_name="demo-project_member.ini",
                    config_path="demo-project_member.ini",
                    port=9002,
                )
            )

    run_db(scenario)


def test_registry_rejects_duplicate_port(settings, run_db):
    async def scenario():
        service = SupervisorRegistryService(settings)
        await service.create(_build_create_data(), operator_id=1, operator_name="ops", remark="测试新增")

        with pytest.raises(PortConflictError):
            await service.ensure_can_create(
                _build_create_data(
                    content_program_name="other_member",
                    file_name="other_member.ini",
                    config_path="other_member.ini",
                    port=9001,
                )
            )

    run_db(scenario)


def test_registry_upsert_imported_uses_config_path_and_preserves_redundant_fields(settings, run_db):
    async def scenario():
        service = SupervisorRegistryService(settings)
        imported = SupervisorRegistryCreateData(
            host_ip="127.0.0.1",
            job_name="saas-java-admincenter",
            module_name="sjfy-admin",
            config_path="saas/legacy-name.ini",
            file_name="legacy-name.ini",
            content_program_name="saas-java-admincenter_sjfy-admin",
            manage_mode=MANAGE_MODE_IMPORTED_READONLY,
            baseline_content="[program:saas-java-admincenter_sjfy-admin]\n",
            metadata_complete=False,
            parse_warnings=("section[program:saas-java-admincenter_sjfy-admin] 存在重复 key: stdout_logfile_maxbytes，已按最后一个值生效",),
            java_path="/usr/local/jdk17/bin/java",
            active_profile="prod",
            port=9200,
            jar_name="sjfy-admin.jar",
            xms="256m",
            xmx="512m",
            run_user="root",
        )

        created, created_flag = await service.upsert_imported(imported, operator_id=0, operator_name="system", remark="导入")
        updated, updated_flag = await service.upsert_imported(
            SupervisorRegistryCreateData(
                **{
                    **imported.__dict__,
                    "baseline_content": "[program:saas-java-admincenter_sjfy-admin]\nuser=root\n",
                    "metadata_complete": True,
                }
            ),
            operator_id=0,
            operator_name="system",
            remark="导入",
        )

        fetched = await service.get_by_config_path_optional("127.0.0.1", "saas/legacy-name.ini")

        assert created_flag is True
        assert updated_flag is False
        assert created.id == updated.id
        assert fetched is not None
        assert fetched.manage_mode == MANAGE_MODE_IMPORTED_READONLY
        assert fetched.config_path == "saas/legacy-name.ini"
        assert fetched.file_name == "legacy-name.ini"
        assert fetched.content_program_name == "saas-java-admincenter_sjfy-admin"
        assert fetched.metadata_complete is True
        assert fetched.baseline_content.endswith("user=root\n")

    run_db(scenario)
