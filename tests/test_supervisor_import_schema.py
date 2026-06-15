"""Supervisor 导入请求模型测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.supervisor import SupervisorImportRequest, SupervisorImportStagingQuery


def test_import_request_accepts_precheck_without_batch_id():
    payload = SupervisorImportRequest(host="127.0.0.1", mode="PRECHECK")

    assert payload.host == "127.0.0.1"
    assert payload.mode == "PRECHECK"
    assert payload.batch_id is None


def test_import_request_rejects_precheck_with_batch_id():
    with pytest.raises(ValidationError):
        SupervisorImportRequest(host="127.0.0.1", mode="PRECHECK", batchId="demo-batch")


def test_import_request_rejects_commit_without_batch_id():
    with pytest.raises(ValidationError):
        SupervisorImportRequest(host="127.0.0.1", mode="COMMIT")


def test_import_request_rejects_commit_with_extra_fields():
    with pytest.raises(ValidationError):
        SupervisorImportRequest.model_validate(
            {
                "host": "127.0.0.1",
                "mode": "COMMIT",
                "batchId": "demo-batch",
                "summary": {},
            }
        )


def test_import_request_rejects_empty_host():
    with pytest.raises(ValidationError):
        SupervisorImportRequest(host="", mode="PRECHECK")


def test_import_request_rejects_invalid_host():
    with pytest.raises(ValidationError):
        SupervisorImportRequest(host="../bad-host", mode="PRECHECK")


@pytest.mark.parametrize("invalid_mode", ["DRY_RUN", "APPLY", "PREVIEW"])
def test_import_request_rejects_invalid_mode(invalid_mode: str):
    with pytest.raises(ValidationError):
        SupervisorImportRequest(host="127.0.0.1", mode=invalid_mode)


def test_import_staging_query_accepts_valid_host():
    payload = SupervisorImportStagingQuery(host="10.1.0.104")

    assert payload.host == "10.1.0.104"


def test_import_staging_query_rejects_invalid_host():
    with pytest.raises(ValidationError):
        SupervisorImportStagingQuery(host="../bad-host")
