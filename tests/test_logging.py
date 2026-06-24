"""日志初始化测试。"""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.logging import configure_logging


def _flush_root_handlers() -> None:
    for handler in logging.getLogger().handlers:
        if hasattr(handler, "flush"):
            handler.flush()


def test_configure_logging_keeps_stdout_only_when_log_path_missing():
    configure_logging("info", log_path=None)

    handler_names = [type(handler).__name__ for handler in logging.getLogger().handlers]

    assert handler_names.count("StreamHandler") == 1
    assert "RotatingFileHandler" not in handler_names


def test_configure_logging_writes_stdout_and_file(tmp_path: Path):
    log_path = tmp_path / "logs" / "app.log"
    configure_logging("info", log_path=log_path)

    logger = logging.getLogger("tests.logging")
    logger.info("日志写入测试")
    _flush_root_handlers()

    assert log_path.exists()
    assert "日志写入测试" in log_path.read_text(encoding="utf-8")


def test_configure_logging_is_idempotent_for_own_handlers(tmp_path: Path):
    log_path = tmp_path / "runtime" / "app.log"

    configure_logging("debug", log_path=log_path)
    configure_logging("debug", log_path=log_path)

    handler_names = [type(handler).__name__ for handler in logging.getLogger().handlers]
    assert handler_names.count("StreamHandler") == 1
    assert handler_names.count("RotatingFileHandler") == 1
