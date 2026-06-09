"""API 行为测试。"""
from __future__ import annotations


TEST_ORIGIN = "http://127.0.0.1:5173"


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
    assert create_data["status"]["state"] == "STOPPED"
    assert (conf_dir / "demo-project_member.ini").exists()
    assert len(fake_mysql.tables["sys_supervisor_service"]) == 1

    list_response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)
    assert list_response.status_code == 200
    list_data = list_response.json()["data"]
    assert len(list_data) == 1
    assert list_data[0]["programName"] == "demo-project_member"
    assert list_data[0]["configPath"] == "demo-project_member.ini"
    assert list_data[0]["fileState"] == "MATCH"
    assert list_data[0]["status"]["state"] == "STOPPED"

    detail_response = client.get(
        "/admin/api/supervisor/services/demo-project_member",
        params={"host": "127.0.0.1"},
        headers=headers,
    )
    assert detail_response.status_code == 200
    detail_data = detail_response.json()["data"]
    assert detail_data["id"] == create_data["id"]
    assert detail_data["jobName"] == "demo-project"
    assert detail_data["moduleName"] == "member"
    assert detail_data["configPath"] == "demo-project_member.ini"
    assert detail_data["fileName"] == "demo-project_member.ini"
    assert detail_data["contentProgramName"] == "demo-project_member"
    assert detail_data["fileState"] == "MATCH"
    assert "[program:demo-project_member]" in detail_data["expectedContent"]


def test_api_list_uses_database_as_source(client, test_environment, seed_user):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    conf_dir.joinpath("manual_only.ini").write_text(test_environment["build_ini"]("manual_only", 9901), encoding="utf-8")

    response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_api_reports_missing_remote_file(client, test_environment, seed_user):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]

    client.post("/admin/api/supervisor/services", json=_payload("127.0.0.1"), headers=headers)
    (conf_dir / "demo-project_member.ini").unlink()

    response = client.get(
        "/admin/api/supervisor/services/demo-project_member",
        params={"host": "127.0.0.1"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["fileState"] == "MISSING"


def test_api_reports_remote_content_mismatch(client, test_environment, seed_user):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]

    client.post("/admin/api/supervisor/services", json=_payload("127.0.0.1"), headers=headers)
    (conf_dir / "demo-project_member.ini").write_text(
        test_environment["build_ini"]("demo-project_member", 9010, job_name="demo-project", module_name="member"),
        encoding="utf-8",
    )

    response = client.get(
        "/admin/api/supervisor/services/demo-project_member",
        params={"host": "127.0.0.1"},
        headers=headers,
    )

    assert response.status_code == 200
    detail_data = response.json()["data"]
    assert detail_data["fileState"] == "MISMATCH"
    assert "9010" in detail_data["remoteContent"]


def test_api_imported_readonly_detail_uses_baseline_content(client, test_environment, seed_user, fake_mysql):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]
    sub_dir = conf_dir / "saas"
    sub_dir.mkdir()
    baseline_content = (
        "[program:legacy_sjfy-admin]\n"
        "command=/usr/local/jdk17/bin/java -jar -Xms256m -Xmx512m "
        "-Dspring.profiles.active=prod -Dserver.port=9200 /data/content/legacy/sjfy-admin.jar\n"
        "directory=/data/content/legacy\n"
        "stdout_logfile_maxbytes=50MB\n"
        "stdout_logfile_maxbytes=1GB\n"
    )
    (sub_dir / "legacy-name.ini").write_text(baseline_content, encoding="utf-8")
    fake_mysql.seed_supervisor_service(
        host_ip="127.0.0.1",
        job_name="legacy",
        module_name="sjfy-admin",
        program_name="legacy_sjfy-admin",
        config_name="legacy-name.ini",
        config_path="saas/legacy-name.ini",
        file_name="legacy-name.ini",
        content_program_name="legacy_sjfy-admin",
        manage_mode="IMPORTED_READONLY",
        baseline_content=baseline_content,
        metadata_complete=False,
        parse_warnings='["section[program:legacy_sjfy-admin] 存在重复 key: stdout_logfile_maxbytes，已按最后一个值生效"]',
        java_path="/usr/local/jdk17/bin/java",
        active_profile="prod",
        port=9200,
        jar_name="sjfy-admin.jar",
        xms="256m",
        xmx="512m",
        run_user=None,
    )

    response = client.get(
        "/admin/api/supervisor/services/legacy_sjfy-admin",
        params={"host": "127.0.0.1"},
        headers=headers,
    )

    assert response.status_code == 200
    detail_data = response.json()["data"]
    assert detail_data["configName"] == "legacy-name.ini"
    assert detail_data["configPath"] == "saas/legacy-name.ini"
    assert detail_data["fileName"] == "legacy-name.ini"
    assert detail_data["contentProgramName"] == "legacy_sjfy-admin"
    assert detail_data["manageMode"] == "IMPORTED_READONLY"
    assert detail_data["metadataComplete"] is False
    assert len(detail_data["parseWarnings"]) == 1
    assert detail_data["expectedContent"] == baseline_content
    assert detail_data["fileState"] == "MATCH"


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
    """验证 DRY_RUN 在服务端 stdout 输出 hostname 探测结果和文件路径。"""
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
