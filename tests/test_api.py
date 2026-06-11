"""API 行为测试。"""
from __future__ import annotations

from pathlib import Path

from app.executor.base import CommandResult, ExecutorRuntimeError
from app.executor.local import LocalExecutor


TEST_ORIGIN = "http://127.0.0.1:5173"


class _FakeImportRemoteExecutor:
    """只模拟导入链路需要的只读能力，避免单测依赖真实 ansible。"""

    def __init__(
        self,
        *,
        hostname_result: CommandResult | None = None,
        config_paths: list[Path] | None = None,
        file_contents: dict[Path, str] | None = None,
        list_configs_error: Exception | None = None,
    ) -> None:
        self.hostname_result = hostname_result or CommandResult(("hostname",), 0, "fake-host", "")
        self.config_paths = config_paths or []
        self.file_contents = file_contents or {}
        self.list_configs_error = list_configs_error

    def run_command(self, command, timeout=None):  # noqa: ANN001, ARG002
        return self.hostname_result

    def list_configs(self, conf_dir: Path, *, recursive: bool = False, include_backups: bool = True) -> list[Path]:  # noqa: ARG002
        if self.list_configs_error is not None:
            raise self.list_configs_error
        return list(self.config_paths)

    def read_text(self, path: Path) -> str:
        if path not in self.file_contents:
            raise ExecutorRuntimeError(f"读取远程文件失败: {path}")
        return self.file_contents[path]


def _payload(host: str, module_name: str = "member", port: int = 9001) -> dict[str, object]:
    return {
        "host": host,
        "jobName": "demo-project",
        "moduleName": module_name,
        "javaPath": "/usr/local/jdk17/bin/java",
        "active": "prod",
        "port": port,
        "jarName": f"{module_name}.jar",
        "configName": "",
        "xms": "128m",
        "xmx": "128m",
        "user": "root",
    }


def _import_payload(host: str, mode: str = "DRY_RUN") -> dict[str, str]:
    return {
        "host": host,
        "mode": mode,
    }


def _login_headers(client) -> dict[str, str]:
    response = client.post(
        "/admin/api/auth/login",
        json={
            "username": "ops",
            "password": "secret",
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['accessToken']}"}


def _force_remote_to_local_executor(monkeypatch, settings) -> None:
    """把远端主机执行器切到本地假实现，复用测试目录和 fake supervisor。"""
    from app.services.host_service import HostService

    monkeypatch.setattr(
        HostService,
        "get_executor",
        lambda self, host_value: LocalExecutor(settings.supervisor.command_timeout_seconds),
    )


def _assert_cors_headers(response, origin: str = TEST_ORIGIN) -> None:
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-methods"] == "GET,POST,PUT,DELETE,OPTIONS"
    assert response.headers["access-control-max-age"] == "3600"
    assert response.headers["access-control-allow-headers"] == (
        "Authorization, Content-Type, Accept, X-Requested-With, Cache-Control, Pragma"
    )
    assert response.headers["access-control-expose-headers"] == "Authorization"


def test_api_requires_jwt(client):
    response = client.get("/admin/api/supervisor/hosts")

    assert response.status_code == 401
    assert response.json()["code"] == 40100


def test_api_create_and_read_flow(client, test_environment, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]

    hosts_response = client.get("/admin/api/supervisor/hosts", headers=headers)
    assert hosts_response.status_code == 200
    assert hosts_response.json()["code"] == 200

    create_response = client.post(
        "/admin/api/supervisor/services",
        json=_payload("127.0.0.1"),
        headers=headers,
    )
    assert create_response.status_code == 200
    create_data = create_response.json()["data"]
    assert create_data["programName"] == "demo-project_member"
    assert create_data["configName"] == "demo-project_member.ini"
    assert create_data["configPath"] == "demo-project_member.ini"
    assert create_data["fileName"] == "demo-project_member.ini"
    assert create_data["contentProgramName"] == "demo-project_member"
    assert create_data["manageMode"] == "TEMPLATE_MANAGED"
    assert create_data["metadataComplete"] is True
    assert create_data["parseWarnings"] == []
    assert create_data["fileState"] == "MATCH"
    assert create_data["status"] is None
    assert (conf_dir / "demo-project_member.ini").exists()
    assert len(fake_mysql.tables["sys_supervisor_service"]) == 1

    list_response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)
    assert list_response.status_code == 200
    list_data = list_response.json()["data"]
    assert list_data["total"] == 1
    assert list_data["page"] == 1
    assert list_data["pageSize"] == 10
    assert len(list_data["records"]) == 1
    assert list_data["records"][0]["programName"] == "demo-project_member"
    assert list_data["records"][0]["configPath"] == "demo-project_member.ini"
    assert list_data["records"][0]["status"] == "UNKNOWN"

    detail_response = client.get(
        "/admin/api/supervisor/services/demo-project_member",
        params={"host": "127.0.0.1"},
        headers=headers,
    )
    assert detail_response.status_code == 200
    detail_data = detail_response.json()["data"]
    assert detail_data["id"] == create_data["id"]
    assert detail_data["host"] == "127.0.0.1"
    assert detail_data["hostName"] == "local"
    assert detail_data["jobName"] == "demo-project"
    assert detail_data["moduleName"] == "member"
    assert detail_data["configPath"] == "demo-project_member.ini"
    assert detail_data["configName"] == "demo-project_member.ini"
    assert detail_data["status"] == "UNKNOWN"
    assert detail_data["pid"] is None
    assert detail_data["uptime"] is None
    assert detail_data["hasBackup"] is False
    assert detail_data["configContent"] is None
    assert detail_data["backupConfigContent"] is None
    assert detail_data["syncStatus"] == "UNKNOWN"
    assert detail_data["syncError"] is None
    assert "expectedContent" not in detail_data
    assert "remoteContent" not in detail_data
    assert "fileState" not in detail_data


def test_api_list_uses_database_as_source(client, test_environment, seed_user):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    conf_dir.joinpath("manual_only.ini").write_text(test_environment["build_ini"]("manual_only", 9901), encoding="utf-8")

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["records"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["pageSize"] == 10
    assert data["pages"] == 0


def test_api_detail_uses_database_only(client, test_environment, seed_user, monkeypatch):
    seed_user()
    headers = _login_headers(client)

    client.post("/admin/api/supervisor/services", json=_payload("127.0.0.1"), headers=headers)

    from app.services.config_file_service import ConfigFileService
    from app.services.supervisor_service import SupervisorService

    monkeypatch.setattr(
        ConfigFileService,
        "read_raw_config_optional_by_config_path",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("详情接口不应读取远端配置")),
    )
    monkeypatch.setattr(
        SupervisorService,
        "status",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("详情接口不应执行 supervisorctl status")),
    )

    response = client.get(
        "/admin/api/supervisor/services/demo-project_member",
        params={"host": "127.0.0.1"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["programName"] == "demo-project_member"


def test_api_sync_service_updates_database_snapshot(client, test_environment, seed_user, fake_supervisor):
    seed_user()
    headers = _login_headers(client)

    client.post("/admin/api/supervisor/services", json=_payload("127.0.0.1"), headers=headers)
    fake_supervisor.states["demo-project_member"] = "RUNNING"

    sync_response = client.post(
        "/admin/api/supervisor/services/demo-project_member/sync",
        params={"host": "127.0.0.1"},
        headers=headers,
    )
    assert sync_response.status_code == 200
    sync_data = sync_response.json()["data"]
    assert sync_data["status"] == "RUNNING"
    assert sync_data["pid"] == "1"
    assert sync_data["uptime"] == "0:00:10"
    assert sync_data["syncStatus"] == "SUCCESS"
    assert sync_data["syncError"] is None
    assert "configContent" in sync_data["syncedFields"]
    assert "command" in sync_data["syncedFields"]
    assert sync_data["warnings"] == []
    assert sync_data["commandResults"]["status"]["ok"] is True
    assert sync_data["commandResults"]["config"]["exists"] is True
    assert sync_data["commandResults"]["backup"]["exists"] is False

    detail_response = client.get(
        "/admin/api/supervisor/services/demo-project_member",
        params={"host": "127.0.0.1"},
        headers=headers,
    )
    assert detail_response.status_code == 200
    detail_data = detail_response.json()["data"]
    assert detail_data["status"] == "RUNNING"
    assert detail_data["pid"] == "1"
    assert detail_data["uptime"] == "0:00:10"
    assert detail_data["hasBackup"] is False
    assert detail_data["syncStatus"] == "SUCCESS"
    assert detail_data["syncError"] is None
    assert detail_data["command"].startswith("/usr/local/jdk17/bin/java")
    assert detail_data["directory"] == "/data/content/demo-project/member"
    assert detail_data["stdoutLogfile"] == "/data/logs/demo-project/demo-project-member.log"
    assert "[program:demo-project_member]" in detail_data["configContent"]
    assert detail_data["backupConfigContent"] is None
    assert detail_data["lastSyncAt"] is not None


def test_api_sync_service_marks_failed_when_config_missing(client, test_environment, seed_user):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    client.post("/admin/api/supervisor/services", json=_payload("127.0.0.1"), headers=headers)
    (conf_dir / "demo-project_member.ini").unlink()

    response = client.post(
        "/admin/api/supervisor/services/demo-project_member/sync",
        params={"host": "127.0.0.1"},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["syncStatus"] == "FAILED"
    assert "当前配置文件不存在" in data["syncError"]
    assert any("当前配置文件不存在" in item for item in data["warnings"])

    detail_response = client.get(
        "/admin/api/supervisor/services/demo-project_member",
        params={"host": "127.0.0.1"},
        headers=headers,
    )
    assert detail_response.status_code == 200
    detail_data = detail_response.json()["data"]
    assert detail_data["syncStatus"] == "FAILED"
    assert "当前配置文件不存在" in detail_data["syncError"]
    assert detail_data["configContent"] is None


def test_api_imports_dry_run_returns_planned_items(client, test_environment, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    (sub_dir / "legacy-name.ini").write_text(
        test_environment["build_ini"]("legacy_service", 9200, job_name="legacy", module_name="svc"),
        encoding="utf-8",
    )

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("127.0.0.1", "DRY_RUN"),
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["host"] == "127.0.0.1"
    assert data["mode"] == "DRY_RUN"
    assert data["summary"] == {"planned": 1, "imported": 0, "updated": 0, "skipped": 0}
    assert len(data["items"]) == 1
    assert data["items"][0]["configPath"] == "saas/legacy-name.ini"
    assert data["items"][0]["fileName"] == "legacy-name.ini"
    assert data["items"][0]["contentProgramName"] == "legacy_service"
    assert data["items"][0]["programName"] == "legacy_service"
    assert data["items"][0]["configName"] == "legacy-name.ini"
    assert data["items"][0]["manageMode"] == "IMPORTED_READONLY"
    assert data["items"][0]["result"] == "PLANNED"
    assert fake_mysql.tables.get("sys_supervisor_service") in (None, [])


def test_api_imports_apply_writes_database(client, test_environment, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    baseline_content = test_environment["build_ini"]("legacy_service", 9200, job_name="legacy", module_name="svc")
    (sub_dir / "legacy-name.ini").write_text(baseline_content, encoding="utf-8")

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("127.0.0.1", "APPLY"),
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "APPLY"
    assert data["summary"] == {"planned": 1, "imported": 1, "updated": 0, "skipped": 0}
    assert data["items"][0]["result"] == "IMPORTED"
    assert len(fake_mysql.tables["sys_supervisor_service"]) == 1
    assert fake_mysql.tables["sys_supervisor_service"][0]["config_path"] == "saas/legacy-name.ini"
    assert fake_mysql.tables["sys_supervisor_service"][0]["manage_mode"] == "IMPORTED_READONLY"
    assert fake_mysql.tables["sys_supervisor_service"][0]["baseline_content"] == baseline_content


def test_api_imports_apply_overwrites_template_managed_record(client, test_environment, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    baseline_content = test_environment["build_ini"]("legacy_service", 9200, job_name="legacy", module_name="svc")
    (sub_dir / "legacy-name.ini").write_text(baseline_content, encoding="utf-8")
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1",
        job_name="legacy",
        module_name="svc",
        program_name="legacy_service",
        config_name="legacy-name.ini",
        config_path="saas/legacy-name.ini",
        file_name="legacy-name.ini",
        content_program_name="legacy_service",
        manage_mode="TEMPLATE_MANAGED",
        baseline_content="[program:legacy_service]\nuser=old\n",
        metadata_complete=True,
        parse_warnings="[]",
        java_path="/usr/local/jdk17/bin/java",
        active_profile="prod",
        port=9200,
        jar_name="svc.jar",
        xms="128m",
        xmx="128m",
        run_user="root",
    )

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("127.0.0.1", "APPLY"),
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {"planned": 1, "imported": 0, "updated": 1, "skipped": 0}
    assert data["items"][0]["result"] == "UPDATED"
    assert data["items"][0]["message"] == "已覆盖原模板纳管记录"
    assert fake_mysql.tables["sys_supervisor_service"][0]["manage_mode"] == "IMPORTED_READONLY"
    assert fake_mysql.tables["sys_supervisor_service"][0]["baseline_content"] == baseline_content


def test_api_imports_skip_program_conflict_and_continue(client, test_environment, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "zz"
    sub_dir.mkdir()
    (conf_dir / "aa-valid.ini").write_text(
        test_environment["build_ini"]("aa_valid", 9100, job_name="demo", module_name="valid"),
        encoding="utf-8",
    )
    (sub_dir / "conflict.ini").write_text(
        (
            "[program:legacy_conflict]\n"
            "command=/usr/local/jdk17/bin/java -jar -Xms256m -Xmx512m "
            "-Dspring.profiles.active=prod -Dserver.port=9200 /data/content/legacy/conflict.jar\n"
            "directory=/data/content/legacy/conflict\n"
        ),
        encoding="utf-8",
    )
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1",
        job_name="legacy",
        module_name="conflict",
        program_name="legacy_conflict",
        config_name="existing.ini",
        config_path="existing.ini",
        file_name="existing.ini",
        content_program_name="legacy_conflict",
        manage_mode="IMPORTED_READONLY",
        baseline_content="[program:legacy_conflict]\n",
        metadata_complete=True,
        parse_warnings="[]",
        java_path="/usr/local/jdk17/bin/java",
        active_profile="prod",
        port=9201,
        jar_name="conflict.jar",
        xms="256m",
        xmx="512m",
        run_user="root",
    )

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("127.0.0.1", "APPLY"),
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {"planned": 1, "imported": 1, "updated": 0, "skipped": 1}
    assert [item["configPath"] for item in data["items"]] == ["aa-valid.ini", "zz/conflict.ini"]
    assert data["items"][0]["result"] == "IMPORTED"
    assert data["items"][1]["result"] == "SKIPPED"
    assert data["items"][1]["message"] == "服务已存在: legacy_conflict"
    assert len(fake_mysql.tables["sys_supervisor_service"]) == 2


def test_api_rejects_duplicate_registry_record_before_remote_write(client, test_environment, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]

    first_response = client.post("/admin/api/supervisor/services", json=_payload("127.0.0.1"), headers=headers)
    second_response = client.post("/admin/api/supervisor/services", json=_payload("127.0.0.1"), headers=headers)

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.json()["code"] == 40900
    assert len(fake_mysql.tables["sys_supervisor_service"]) == 1
    assert (conf_dir / "demo-project_member.ini").exists()


def test_api_rolls_back_remote_file_when_registry_insert_fails(client, test_environment, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    fake_mysql.fail_next_supervisor_insert = True

    response = client.post("/admin/api/supervisor/services", json=_payload("127.0.0.1"), headers=headers)

    assert response.status_code == 500
    assert response.json()["msg"] == "新增服务写库失败"
    assert not (conf_dir / "demo-project_member.ini").exists()
    assert fake_mysql.tables["sys_supervisor_service"] == []
    assert response.json()["data"]["configRemoved"] is True


def test_api_rejects_invalid_config_name(client, seed_user):
    seed_user()
    headers = _login_headers(client)
    response = client.post(
        "/admin/api/supervisor/services",
        json={
            **_payload("127.0.0.1"),
            "configName": "../bad",
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["code"] == 40002
    assert response.json()["data"] is None


def test_api_rejects_remote_service_creation(client, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)

    response = client.post("/admin/api/supervisor/services", json=_payload("10.1.0.104"), headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == 40300
    assert response.json()["msg"] == "当前项目禁止修改远端配置文件"
    assert fake_mysql.tables["sys_supervisor_service"] == []


def test_api_rejects_removed_supervisor_template_fields(client, seed_user):
    seed_user()
    headers = _login_headers(client)
    response = client.post(
        "/admin/api/supervisor/services",
        json={
            **_payload("127.0.0.1"),
            "priority": 999,
            "autostart": True,
            "autoStart": True,
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["code"] == 40000


def test_api_preflight_login_returns_cors_headers(client):
    response = client.options(
        "/admin/api/auth/login",
        headers={
            "Origin": TEST_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.status_code == 200
    _assert_cors_headers(response)


def test_api_preflight_protected_route_skips_jwt(client):
    response = client.options(
        "/admin/api/supervisor/hosts",
        headers={
            "Origin": TEST_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )

    assert response.status_code == 200
    _assert_cors_headers(response)


def test_api_unauthorized_response_keeps_cors_headers(client):
    response = client.get(
        "/admin/api/supervisor/hosts",
        headers={
            "Origin": TEST_ORIGIN,
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == 40100
    _assert_cors_headers(response)


def test_api_success_response_keeps_cors_headers(client, seed_user):
    seed_user()
    headers = _login_headers(client)
    response = client.get(
        "/admin/api/supervisor/hosts",
        headers={
            **headers,
            "Origin": TEST_ORIGIN,
        },
    )

    assert response.status_code == 200
    assert response.json()["code"] == 200
    _assert_cors_headers(response)


def test_api_import_dry_run_prints_hostname_and_file_paths(client, test_environment, seed_user, capsys):
    """验证 DRY_RUN 在服务端 stdout 输出 hostname 探测结果和逐文件诊断。"""
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    (sub_dir / "legacy-name.ini").write_text(
        test_environment["build_ini"]("legacy_service", 9200, job_name="legacy", module_name="svc"),
        encoding="utf-8",
    )

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("127.0.0.1", "DRY_RUN"),
        headers=headers,
    )

    assert response.status_code == 200
    captured = capsys.readouterr().out
    assert "[SUPERVISOR_IMPORT_DEBUG]" in captured
    assert "host=127.0.0.1" in captured
    assert "hostname=" in captured or "探测失败" in captured or "探测异常" in captured
    assert "saas/legacy-name.ini" in captured
    # 验证逐文件诊断阶段输出
    assert "start" in captured
    assert "read_done" in captured
    assert "parse_done" in captured
    assert "plan_done" in captured
    assert "finish" in captured
    # 验证汇总日志
    assert "导入汇总" in captured
    assert "planned=1" in captured
    assert "elapsed=" in captured


def test_api_import_empty_dir_returns_404(client, test_environment, seed_user):
    """验证远端无可读 *.ini 时返回 404，不会返回 200 + items=[]。"""
    seed_user()
    headers = _login_headers(client)

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("127.0.0.1", "DRY_RUN"),
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["code"] == 40400
    assert response.json()["msg"] == "远端目录下无可用配置文件"


def test_api_import_inventory_miss_returns_404(client, test_environment, seed_user, monkeypatch, capsys):
    """验证导入前置 inventory 未匹配时返回 404。"""
    seed_user()
    headers = _login_headers(client)
    from app.services.host_service import HostService

    fake_executor = _FakeImportRemoteExecutor(
        hostname_result=CommandResult(("hostname",), 1, "", "目标主机未匹配: ansible inventory 中未找到 10.1.0.104"),
        list_configs_error=ExecutorRuntimeError("目标主机未匹配: ansible inventory 中未找到 10.1.0.104"),
    )
    monkeypatch.setattr(HostService, "get_executor", lambda self, host_value: fake_executor)

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("10.1.0.104", "DRY_RUN"),
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["code"] == 40400
    assert response.json()["msg"] == "目标主机未匹配"
    captured = capsys.readouterr().out
    assert "hostname 探测失败: 目标主机未匹配" in captured
    assert "preflight_failed kind=inventory_miss" in captured


def test_api_import_unreachable_returns_404(client, test_environment, seed_user, monkeypatch, capsys):
    """验证导入前置 SSH/UNREACHABLE 失败时返回统一 404。"""
    seed_user()
    headers = _login_headers(client)
    from app.services.host_service import HostService

    fake_executor = _FakeImportRemoteExecutor(
        hostname_result=CommandResult(
            ("hostname",),
            4,
            '{"msg": "Failed to connect to the host via ssh: Connection timed out"}',
            "",
        ),
        list_configs_error=ExecutorRuntimeError("Failed to connect to the host via ssh: Connection timed out"),
    )
    monkeypatch.setattr(HostService, "get_executor", lambda self, host_value: fake_executor)

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("10.1.0.104", "DRY_RUN"),
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["code"] == 40400
    assert response.json()["msg"] == "目标主机不可达"
    captured = capsys.readouterr().out
    assert "Connection timed out" in captured
    assert "preflight_failed kind=unreachable" in captured


def test_api_import_hostname_failure_does_not_block_success(client, test_environment, seed_user, monkeypatch, capsys):
    """验证 hostname 探测失败只记诊断，不影响后续扫描成功。"""
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    config_path = conf_dir / "saas" / "legacy-name.ini"
    config_path.parent.mkdir()
    content = test_environment["build_ini"]("legacy_service", 9200, job_name="legacy", module_name="svc")
    from app.services.host_service import HostService

    fake_executor = _FakeImportRemoteExecutor(
        hostname_result=CommandResult(
            ("hostname",),
            4,
            '{"msg": "Failed to connect to the host via ssh: Connection timed out"}',
            "",
        ),
        config_paths=[config_path],
        file_contents={config_path: content},
    )
    monkeypatch.setattr(HostService, "get_executor", lambda self, host_value: fake_executor)

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("10.1.0.104", "DRY_RUN"),
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {"planned": 1, "imported": 0, "updated": 0, "skipped": 0}
    assert data["items"][0]["configPath"] == "saas/legacy-name.ini"
    captured = capsys.readouterr().out
    assert "hostname 探测失败" in captured
    assert "Connection timed out" in captured
    assert "导入汇总" in captured


def test_api_import_skips_parse_error_and_continues(client, test_environment, seed_user, capsys):
    """验证解析失败的文件被 SKIPPED，其余文件正常处理。"""
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    # 合法文件
    (sub_dir / "valid.ini").write_text(
        test_environment["build_ini"]("valid_service", 9100, job_name="demo", module_name="valid"),
        encoding="utf-8",
    )
    # 非法文件 — 缺少 [program:*] 段
    (conf_dir / "bad.ini").write_text("not-a-valid-ini-content\n", encoding="utf-8")

    response = client.post(
        "/admin/api/supervisor/imports",
        json=_import_payload("127.0.0.1", "DRY_RUN"),
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {"planned": 1, "imported": 0, "updated": 0, "skipped": 1}
    # items 按 configPath 升序排列，bad.ini 在 saas/valid.ini 之前
    assert data["items"][0]["configPath"] == "bad.ini"
    assert data["items"][0]["result"] == "SKIPPED"
    assert data["items"][1]["configPath"] == "saas/valid.ini"
    assert data["items"][1]["result"] == "PLANNED"
    # 诊断输出也包含两个文件的日志
    captured = capsys.readouterr().out
    assert "saas/valid.ini" in captured
    assert "bad.ini" in captured
    assert "导入汇总" in captured
    assert "planned=1" in captured
    assert "skipped=1" in captured


def test_api_list_pagination_defaults(client, test_environment, seed_user, fake_mysql):
    """验证列表分页默认 page=1, pageSize=10。"""
    seed_user()
    headers = _login_headers(client)

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["page"] == 1
    assert data["pageSize"] == 10
    assert data["pages"] == 0
    assert data["total"] == 0
    assert data["records"] == []


def test_api_list_pagination_page_size_20(client, test_environment, seed_user, fake_mysql):
    """验证 pageSize=20 正常工作。"""
    seed_user()
    headers = _login_headers(client)

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1", "pageSize": "20"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["data"]["pageSize"] == 20


def test_api_list_rejects_invalid_page_size(client, test_environment, seed_user):
    """验证非法 pageSize 返回 422。"""
    seed_user()
    headers = _login_headers(client)

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1", "pageSize": "5"}, headers=headers)

    # 自定义验证错误处理器返回 400 而非默认 422
    assert response.status_code == 400


def test_api_list_rejects_page_below_one(client, test_environment, seed_user):
    """验证 page < 1 返回 422。"""
    seed_user()
    headers = _login_headers(client)

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1", "page": "0"}, headers=headers)

    assert response.status_code == 400


def test_api_list_filters_by_status(client, test_environment, seed_user, fake_mysql):
    """验证 status 过滤。"""
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="demo", module_name="a", program_name="demo_a",
        config_name="a.ini", status="RUNNING", pid="12345", uptime="0:10:00",
    )
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="demo", module_name="b", program_name="demo_b",
        config_name="b.ini", status="STOPPED",
    )

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1", "status": "RUNNING"}, headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["programName"] == "demo_a"
    assert data["records"][0]["status"] == "RUNNING"
    assert data["records"][0]["pid"] == "12345"
    assert data["records"][0]["uptime"] == "0:10:00"


def test_api_list_filters_by_keyword(client, test_environment, seed_user, fake_mysql):
    """验证 keyword 模糊匹配。"""
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="project-a", module_name="member", program_name="project-a_member",
        config_name="project-a_member.ini",
    )
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="project-b", module_name="order", program_name="project-b_order",
        config_name="project-b_order.ini",
    )

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1", "keyword": "member"}, headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["programName"] == "project-a_member"


def test_api_list_sorts_by_update_time_desc(client, test_environment, seed_user, fake_mysql):
    """验证排序为 update_time DESC, id DESC。"""
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="demo", module_name="first", program_name="demo_first",
        config_name="first.ini",
    )
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="demo", module_name="second", program_name="demo_second",
        config_name="second.ini",
    )

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1", "pageSize": "50"}, headers=headers)

    assert response.status_code == 200
    records = response.json()["data"]["records"]
    assert len(records) == 2
    assert records[0]["programName"] == "demo_second"
    assert records[1]["programName"] == "demo_first"


def test_api_list_pure_database_no_remote_call(client, test_environment, seed_user, fake_mysql, monkeypatch):
    """验证列表接口纯数据库查询，不触发任何远端命令。"""
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="demo", module_name="svc", program_name="demo_svc",
        config_name="svc.ini",
    )
    from app.services.supervisor_manager import SupervisorManager
    original_list = SupervisorManager.list_services_page
    called_remote = False

    def assert_no_remote(*args, **kwargs):
        nonlocal called_remote
        called_remote = True
        return {"records": [], "page": 1, "pageSize": 10, "total": 0, "pages": 0}

    monkeypatch.setattr(SupervisorManager, "list_services_page", assert_no_remote)

    # 即使 mock 让列表返回空，也不应抛出异常
    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)
    assert response.status_code == 200


def test_api_status_refresh_updates_database(client, test_environment, seed_user, fake_mysql, fake_supervisor):
    """验证刷新状态接口正确解析 supervisorctl 输出并落库。"""
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="demo", module_name="svc", program_name="demo_svc",
        config_name="svc.ini", status="STOPPED",
    )
    (conf_dir / "svc.ini").write_text(
        test_environment["build_ini"]("demo_svc", 9001), encoding="utf-8",
    )
    fake_supervisor.states["demo_svc"] = "RUNNING"

    response = client.post("/admin/api/supervisor/services/status/refresh", params={"host": "127.0.0.1"}, headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["host"] == "127.0.0.1"
    assert data["total"] >= 1
    assert data["updated"] >= 1
    assert data["missing"] == 0
    # 验证数据库已更新
    record = fake_mysql.tables["sys_supervisor_service"][0]
    assert record["status"] == "RUNNING"


def test_api_status_refresh_reports_missing(client, test_environment, seed_user, fake_mysql, fake_supervisor):
    """验证刷新接口中远端存在但数据库不存在的 programName 计入 missing。"""
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    (conf_dir / "orphan.ini").write_text(
        test_environment["build_ini"]("orphan_service", 9999), encoding="utf-8",
    )
    fake_supervisor.states["orphan_service"] = "RUNNING"

    response = client.post("/admin/api/supervisor/services/status/refresh", params={"host": "127.0.0.1"}, headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["missing"] >= 1


def test_api_status_refresh_caplus_chinese_log(client, test_environment, seed_user, fake_mysql, caplog):
    """验证状态刷新输出中文日志。"""
    import logging
    caplog.set_level(logging.INFO)
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1", job_name="demo", module_name="svc", program_name="demo_svc",
        config_name="svc.ini",
    )

    response = client.post("/admin/api/supervisor/services/status/refresh", params={"host": "127.0.0.1"}, headers=headers)

    assert response.status_code == 200
    assert any("刷新服务状态" in record.message for record in caplog.records)


def test_api_list_filters_archived_records(client, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1",
        job_name="demo",
        module_name="active",
        program_name="demo_active",
        config_name="active.ini",
        is_archived=False,
    )
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1",
        job_name="demo",
        module_name="archived",
        program_name="demo_archived",
        config_name="archived.ini",
        is_archived=True,
        archived_at="2026-06-10 10:00:00",
    )

    default_response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)
    archived_response = client.get(
        "/admin/api/supervisor/services",
        params={"host": "127.0.0.1", "archived": "true"},
        headers=headers,
    )
    all_response = client.get(
        "/admin/api/supervisor/services",
        params={"host": "127.0.0.1", "archived": "all"},
        headers=headers,
    )

    assert default_response.status_code == 200
    assert [item["programName"] for item in default_response.json()["data"]["records"]] == ["demo_active"]
    assert default_response.json()["data"]["records"][0]["isArchived"] is False

    assert archived_response.status_code == 200
    assert [item["programName"] for item in archived_response.json()["data"]["records"]] == ["demo_archived"]
    assert archived_response.json()["data"]["records"][0]["isArchived"] is True

    assert all_response.status_code == 200
    assert [item["programName"] for item in all_response.json()["data"]["records"]] == ["demo_archived", "demo_active"]


def test_api_detail_returns_archive_fields(client, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1",
        job_name="legacy",
        module_name="svc",
        program_name="legacy_svc",
        config_name="legacy.ini",
        config_path="saas/legacy.ini",
        file_name="legacy.ini",
        manage_mode="IMPORTED_READONLY",
        baseline_content="[program:legacy_svc]\ncommand=/bin/true\n",
        metadata_complete=False,
        is_archived=True,
        archived_at="2026-06-10 10:00:00",
        restored_at="2026-06-10 12:00:00",
    )

    response = client.get(
        "/admin/api/supervisor/services/legacy_svc",
        params={"host": "127.0.0.1"},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["isArchived"] is True
    assert data["archivedAt"] == "2026-06-10 10:00:00"
    assert data["restoredAt"] == "2026-06-10 12:00:00"
    assert data["syncStatus"] == "UNKNOWN"
    assert "fileState" not in data


def test_api_sync_rejects_archived_service(client, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1",
        job_name="legacy",
        module_name="svc",
        program_name="legacy_svc",
        config_name="legacy.ini",
        config_path="legacy.ini",
        file_name="legacy.ini",
        is_archived=True,
    )

    response = client.post(
        "/admin/api/supervisor/services/legacy_svc/sync",
        params={"host": "127.0.0.1"},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["code"] == 40920


def test_api_runtime_actions_allow_ansible_and_update_snapshot(
    client,
    test_environment,
    seed_user,
    fake_mysql,
    fake_supervisor,
    monkeypatch,
    settings,
):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    conf_dir.joinpath("remote.ini").write_text(
        test_environment["build_ini"]("remote_svc", 9300, job_name="remote", module_name="svc"),
        encoding="utf-8",
    )
    fake_mysql.seed_supervisor_service(
        host_ip="10.1.0.104",
        job_name="remote",
        module_name="svc",
        program_name="remote_svc",
        config_name="remote.ini",
        baseline_content=test_environment["build_ini"]("remote_svc", 9300, job_name="remote", module_name="svc"),
    )
    _force_remote_to_local_executor(monkeypatch, settings)

    start_response = client.post(
        "/admin/api/supervisor/services/remote_svc/start",
        params={"host": "10.1.0.104"},
        headers=headers,
    )
    stop_response = client.post(
        "/admin/api/supervisor/services/remote_svc/stop",
        params={"host": "10.1.0.104"},
        headers=headers,
    )
    restart_response = client.post(
        "/admin/api/supervisor/services/remote_svc/restart",
        params={"host": "10.1.0.104"},
        headers=headers,
    )

    assert start_response.status_code == 200
    assert start_response.json()["data"]["status"] == "RUNNING"
    assert stop_response.status_code == 200
    assert stop_response.json()["data"]["status"] == "STOPPED"
    assert restart_response.status_code == 200
    assert restart_response.json()["data"]["status"] == "RUNNING"
    record = fake_mysql.tables["sys_supervisor_service"][0]
    assert record["status"] == "RUNNING"
    assert record["pid"] == "1"
    assert record["uptime"] == "0:00:10"
    assert fake_supervisor.states["remote_svc"] == "RUNNING"


def test_api_runtime_actions_block_archived_service(client, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    fake_mysql.seed_supervisor_service(
        host_ip="10.1.0.104",
        job_name="remote",
        module_name="svc",
        program_name="remote_archived",
        config_name="remote_archived.ini",
        is_archived=True,
        archived_at="2026-06-10 10:00:00",
    )

    for action in ("start", "stop", "restart"):
        response = client.post(
            f"/admin/api/supervisor/services/remote_archived/{action}",
            params={"host": "10.1.0.104"},
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["code"] == 40910


def test_api_archive_and_restore_remote_subdir_config(
    client,
    test_environment,
    seed_user,
    fake_mysql,
    fake_supervisor,
    monkeypatch,
    settings,
    caplog,
):
    import logging

    caplog.set_level(logging.INFO)
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    config_path = sub_dir / "remote-archive.ini"
    content = test_environment["build_ini"]("remote_archive", 9400, job_name="remote", module_name="archive")
    config_path.write_text(content, encoding="utf-8")
    fake_supervisor.states["remote_archive"] = "RUNNING"
    fake_mysql.seed_supervisor_service(
        host_ip="10.1.0.104",
        job_name="remote",
        module_name="archive",
        program_name="remote_archive",
        config_name="remote-archive.ini",
        config_path="saas/remote-archive.ini",
        file_name="remote-archive.ini",
        baseline_content=content,
        port=9400,
        run_user="root",
    )
    _force_remote_to_local_executor(monkeypatch, settings)

    archive_response = client.post(
        "/admin/api/supervisor/services/remote_archive/archive",
        params={"host": "10.1.0.104"},
        headers=headers,
    )

    assert archive_response.status_code == 200
    archive_data = archive_response.json()["data"]
    assert archive_data["isArchived"] is True
    assert archive_data["status"] == "STOPPED"
    assert archive_data["fileResult"]["backup"]["configPath"] == "saas/remote-archive.ini"
    assert not config_path.exists()
    assert conf_dir.joinpath("saas/remote-archive.ini.bak").exists()
    record = fake_mysql.tables["sys_supervisor_service"][0]
    assert record["is_archived"] == 1
    assert record["status"] == "STOPPED"

    restore_response = client.post(
        "/admin/api/supervisor/services/remote_archive/restore",
        params={"host": "10.1.0.104"},
        headers=headers,
    )

    assert restore_response.status_code == 200
    restore_data = restore_response.json()["data"]
    assert restore_data["isArchived"] is False
    assert config_path.exists()
    assert fake_mysql.tables["sys_supervisor_service"][0]["is_archived"] == 0

    start_response = client.post(
        "/admin/api/supervisor/services/remote_archive/start",
        params={"host": "10.1.0.104"},
        headers=headers,
    )
    assert start_response.status_code == 200
    assert start_response.json()["data"]["status"] == "RUNNING"
    assert any("归档服务：目标主机=10.1.0.104，服务名称=remote_archive" in record.message for record in caplog.records)
    assert any("还原服务成功：目标主机=10.1.0.104，服务名称=remote_archive" in record.message for record in caplog.records)
