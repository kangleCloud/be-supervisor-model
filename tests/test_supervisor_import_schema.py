"""Supervisor 导入请求模型测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.supervisor import SupervisorImportRequest


def test_import_request_rejects_empty_host():
    with pytest.raises(ValidationError):
        SupervisorImportRequest(host="", mode="DRY_RUN")


def test_import_request_rejects_invalid_host():
    with pytest.raises(ValidationError):
        SupervisorImportRequest(host="../bad-host", mode="DRY_RUN")


def test_import_request_rejects_invalid_mode():
    with pytest.raises(ValidationError):
        SupervisorImportRequest(host="127.0.0.1", mode="PREVIEW")
