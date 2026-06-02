"""安全校验测试。"""
from __future__ import annotations

import pytest

from app.core.exceptions import InvalidConfigNameError, ParamError
from app.core.security import ensure_safe_program_name, normalize_config_name
from app.schemas.supervisor import ServiceUpsertRequest


def test_reject_invalid_program_name():
    with pytest.raises(ParamError):
        ensure_safe_program_name("demo;rm -rf /")


def test_reject_path_traversal_config_name():
    with pytest.raises(InvalidConfigNameError):
        normalize_config_name("../demo", "demo")


def test_request_rejects_invalid_module_name():
    with pytest.raises(ParamError):
        ServiceUpsertRequest(
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
