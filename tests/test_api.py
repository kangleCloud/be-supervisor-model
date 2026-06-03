"""API 行为测试。"""
from __future__ import annotations


def _payload(host: str, module_name: str = "member", port: int = 9001, auto_start: bool = False) -> dict[str, object]:
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
        "autoStart": auto_start,
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


def test_api_requires_jwt(client):
    response = client.get("/admin/api/supervisor/hosts")

    assert response.status_code == 401
    assert response.json()["code"] == 40100


def test_api_crud_flow(client, test_environment, seed_user):
    seed_user()
    headers = _login_headers(client)
    conf_dir = test_environment["conf_dir"]

    hosts_response = client.get("/admin/api/supervisor/hosts", headers=headers)
    assert hosts_response.status_code == 200
    assert hosts_response.json()["code"] == 200

    create_response = client.post(
        "/admin/api/supervisor/services",
        json=_payload("127.0.0.1", auto_start=True),
        headers=headers,
    )
    assert create_response.status_code == 200
    assert create_response.json()["data"]["programName"] == "demo-project_member"
    assert (conf_dir / "demo-project_member.ini").exists()

    list_response = client.get("/admin/api/supervisor/services", params={"host": "127.0.0.1"}, headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["data"][0]["programName"] == "demo-project_member"

    detail_response = client.get(
        "/admin/api/supervisor/services/demo-project_member",
        params={"host": "127.0.0.1"},
        headers=headers,
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["parsed"]["port"] == 9001

    status_response = client.get(
        "/admin/api/supervisor/status",
        params={"host": "127.0.0.1", "programName": "demo-project_member"},
        headers=headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["data"][0]["state"] == "RUNNING"

    update_response = client.put(
        "/admin/api/supervisor/services/demo-project_member",
        json=_payload("127.0.0.1", module_name="member-v2", port=9002),
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["data"]["newProgramName"] == "demo-project_member-v2"
    assert (conf_dir / "demo-project_member-v2.ini").exists()
    assert not (conf_dir / "demo-project_member.ini").exists()

    backup_response = client.post(
        "/admin/api/supervisor/services/demo-project_member-v2/backup",
        json={"host": "127.0.0.1"},
        headers=headers,
    )
    assert backup_response.status_code == 200
    assert (conf_dir / "demo-project_member-v2.ini.bak").exists()

    restore_response = client.post(
        "/admin/api/supervisor/services/demo-project_member-v2/restore",
        json={"host": "127.0.0.1"},
        headers=headers,
    )
    assert restore_response.status_code == 200

    restart_response = client.post(
        "/admin/api/supervisor/services/demo-project_member-v2/restart",
        json={"host": "127.0.0.1"},
        headers=headers,
    )
    assert restart_response.status_code == 200

    delete_response = client.delete(
        "/admin/api/supervisor/services/demo-project_member-v2",
        params={"host": "127.0.0.1", "deleteBackup": "true"},
        headers=headers,
    )
    assert delete_response.status_code == 200
    assert not (conf_dir / "demo-project_member-v2.ini").exists()


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
            "autorestart": True,
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["code"] == 40000
