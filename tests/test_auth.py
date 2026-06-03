"""认证接口测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt


def _login(client, username: str = "ops", password: str = "secret"):
    return client.post(
        "/admin/api/auth/login",
        json={
            "username": username,
            "password": password,
        },
    )


def test_auth_login_and_me_success(client, seed_user, fake_mysql):
    seed_user(is_super_admin=1, nick_name="超级运维")

    login_response = _login(client)

    assert login_response.status_code == 200
    payload = login_response.json()["data"]
    assert payload["tokenType"] == "Bearer"
    assert payload["user"]["displayName"] == "超级运维"
    assert payload["user"]["roles"] == ["SUPER_ADMIN"]
    assert len(fake_mysql.tables["sys_login_session"]) == 1
    assert len(fake_mysql.tables["sys_login_log"]) == 1
    assert fake_mysql.tables["sys_login_log"][0]["status"] == "0"

    me_response = client.get(
        "/admin/api/auth/me",
        headers={"Authorization": f"Bearer {payload['accessToken']}"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["data"]["permissions"] == ["supervisor:manage"]


def test_auth_rejects_invalid_password(client, seed_user, fake_mysql):
    seed_user()

    response = _login(client, password="bad-password")

    assert response.status_code == 401
    assert response.json()["msg"] == "用户名或密码错误"
    assert len(fake_mysql.tables["sys_login_log"]) == 1
    assert fake_mysql.tables["sys_login_log"][0]["status"] == "1"


def test_auth_rejects_disabled_user(client, seed_user, fake_mysql):
    seed_user(status=0)

    response = _login(client)

    assert response.status_code == 401
    assert response.json()["msg"] == "账号已禁用"
    assert len(fake_mysql.tables["sys_login_log"]) == 1
    assert fake_mysql.tables["sys_login_log"][0]["msg"] == "账号已禁用"


def test_auth_rejects_invalid_signature(client, seed_user):
    seed_user()
    login_response = _login(client)
    access_token = login_response.json()["data"]["accessToken"]
    broken_token = f"{access_token}broken"

    response = client.get(
        "/admin/api/auth/me",
        headers={"Authorization": f"Bearer {broken_token}"},
    )

    assert response.status_code == 401
    assert response.json()["msg"] == "登录凭证无效"


def test_auth_rejects_expired_token(client, seed_user, settings):
    seed_user(user_id=9)
    expired_token = jwt.encode(
        {
            "sub": "9",
            "username": "ops",
            "jti": "expired-jti",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.auth.jwt_secret,
        algorithm="HS256",
    )

    response = client.get(
        "/admin/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert response.status_code == 401
    assert response.json()["msg"] == "登录状态已过期"


def test_auth_logout_revokes_session(client, seed_user):
    seed_user()
    login_response = _login(client)
    access_token = login_response.json()["data"]["accessToken"]
    headers = {"Authorization": f"Bearer {access_token}"}

    logout_response = client.post("/admin/api/auth/logout", headers=headers)
    assert logout_response.status_code == 200

    me_response = client.get("/admin/api/auth/me", headers=headers)
    assert me_response.status_code == 401
    assert me_response.json()["msg"] == "登录会话已失效"
