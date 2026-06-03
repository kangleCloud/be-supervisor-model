"""数据库初始化与鉴权落库测试。"""
from __future__ import annotations

from app.core.database import initialize_database


def test_database_initialization_is_idempotent(settings, fake_mysql):
    initialize_database(settings)
    initialize_database(settings)

    assert settings.database.database in fake_mysql.databases
    assert "sys_user" in fake_mysql.tables
    assert "sys_login_log" in fake_mysql.tables
    assert "sys_login_session" in fake_mysql.tables
    assert len(fake_mysql.tables["sys_schema_migration"]) == 1
    assert fake_mysql.tables["sys_schema_migration"][0]["version"] == 1


def test_login_persists_session_and_log(client, seed_user, fake_mysql):
    seed_user()

    response = client.post(
        "/admin/api/auth/login",
        json={
            "username": "ops",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    assert len(fake_mysql.tables["sys_login_session"]) == 1
    assert fake_mysql.tables["sys_login_session"][0]["token_digest"]
    assert len(fake_mysql.tables["sys_login_log"]) == 1
    assert fake_mysql.tables["sys_login_log"][0]["token_jti"] == fake_mysql.tables["sys_login_session"][0]["token_jti"]
