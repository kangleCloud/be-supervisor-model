"""安全校验测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import InvalidConfigNameError, ParamError
from app.core.security import ensure_safe_path_under_dir, ensure_safe_program_name, normalize_config_name, normalize_config_path
from app.schemas.supervisor import ServiceCreateRequest


def test_reject_invalid_program_name():
    with pytest.raises(ParamError):
        ensure_safe_program_name("demo;rm -rf /")


def test_reject_path_traversal_config_name():
    with pytest.raises(InvalidConfigNameError):
        normalize_config_name("../demo", "demo")


def test_safe_path_under_dir_preserves_literal_absolute_path():
    result = ensure_safe_path_under_dir(
        Path("/etc/supervisord.d"),
        Path("/etc/supervisord.d/demo_member.ini"),
    )

    assert result == Path("/etc/supervisord.d/demo_member.ini")


def test_safe_path_under_dir_rejects_normalized_escape():
    with pytest.raises(InvalidConfigNameError):
        ensure_safe_path_under_dir(
            Path("/etc/supervisord.d"),
            Path("/etc/supervisord.d/../passwd"),
        )


def test_normalize_config_path_allows_subdirectories():
    assert normalize_config_path("saas/demo_member.ini") == "saas/demo_member.ini"


def test_normalize_config_path_rejects_traversal():
    with pytest.raises(InvalidConfigNameError):
        normalize_config_path("../saas/demo_member.ini")


def test_request_rejects_invalid_module_name():
    with pytest.raises(ParamError):
        ServiceCreateRequest(
            host="127.0.0.1",
            jobName="demo",
            moduleName="../member",
            javaPath="/usr/local/jdk17/bin/java",
            active="prod",
            port=9001,
            jarName="member.jar",
            configName="",
            xms="128m",
            xmx="128m",
            user="root",
        )
