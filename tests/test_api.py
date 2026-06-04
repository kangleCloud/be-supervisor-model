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
    assert create_data["fileState"] == "MATCH"
    assert create_data["status"]["state"] == "STOPPED"
    assert (conf_dir / "demo-project_member.ini").exists()
    assert len(fake_mysql.tables["sys_supervisor_service"]) == 1

    list_response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)
    assert list_response.status_code == 200
    list_data = list_response.json()["data"]
    assert len(list_data) == 1
    assert list_data[0]["programName"] == "demo-project_member"
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
