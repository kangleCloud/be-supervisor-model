"""通用格式化工具。"""
from __future__ import annotations


def format_datetime_text(value: object) -> str | None:
    """兼容真实 datetime 与测试夹具中的字符串时间。"""
    if value in (None, ""):
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)
