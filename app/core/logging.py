"""日志初始化。"""
from __future__ import annotations

import logging


def configure_logging(level: str) -> None:
    """初始化基础日志配置。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
