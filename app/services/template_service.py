"""Supervisor 模板渲染与解析。"""
from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.core.config import Settings
from app.core.exceptions import ParamError
from app.core.security import ensure_safe_name, ensure_valid_port, normalize_config_name
from app.schemas.supervisor import ServiceCreateRequest


PORT_PATTERN = re.compile(r"(?:-Dserver\.port=|server\.port=|port=)(?P<port>\d+)")
PROFILE_PATTERN = re.compile(r"-Dspring\.profiles\.active=(?P<value>\S+)")
XMS_PATTERN = re.compile(r"-Xms(?P<value>\S+)")
XMX_PATTERN = re.compile(r"-Xmx(?P<value>\S+)")
JAR_PATTERN = re.compile(r"(?P<value>/\S+\.jar)")

FIXED_TEMPLATE_OPTIONS: dict[str, Any] = {
    "autostart": "true",
    "startsecs": 10,
    "autorestart": "true",
    "startretries": 3,
    "priority": 999,
    "redirect_stderr": "true",
    "stdout_logfile_maxbytes": "1GB",
    "stdout_logfile_backups": 1,
    "stopasgroup": "false",
    "killasgroup": "false",
}


@dataclass(frozen=True)
class RenderedConfig:
    """渲染后的配置文件结果。"""

    program_name: str
    config_name: str
    content: str


@dataclass(frozen=True)
class ParsedConfig:
    """结构化配置。"""

    program_name: str
    options: dict[str, str]
    job_name: Optional[str]
    module_name: Optional[str]
    port: Optional[int]
    java_path: Optional[str]
    active: Optional[str]
    jar_name: Optional[str]
    xms: Optional[str]
    xmx: Optional[str]
    run_user: Optional[str]


class TemplateService:
    """负责 Supervisor 配置模板渲染与反向解析。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.environment = Environment(
            loader=FileSystemLoader(str(settings.repo_root / "app" / "templates")),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
        )

    @staticmethod
    def build_program_name(job_name: str, module_name: str) -> str:
        safe_job_name = ensure_safe_name(job_name, "jobName")
        safe_module_name = ensure_safe_name(module_name, "moduleName")
        return f"{safe_job_name}_{safe_module_name}"

    @staticmethod
    def build_default_jar_name(module_name: str) -> str:
        safe_module_name = ensure_safe_name(module_name, "moduleName")
        return f"{safe_module_name}.jar"

    def build_config_name(self, config_name: str, program_name: str) -> str:
        return normalize_config_name(config_name, program_name)

    def render(self, payload: ServiceCreateRequest) -> RenderedConfig:
        """根据新增请求渲染 Supervisor 模板。"""
        return self.render_service(
            job_name=payload.job_name,
            module_name=payload.module_name,
            java_path=payload.java_path,
            active=payload.active,
            port=payload.port,
            jar_name=payload.jar_name,
            config_name=payload.config_name,
            xms=payload.xms,
            xmx=payload.xmx,
            user=payload.user,
        )

    def render_service(
        self,
        *,
        job_name: str,
        module_name: str,
        java_path: str,
        active: str,
        port: int,
        jar_name: str,
        config_name: str,
        xms: str,
        xmx: str,
        user: str,
    ) -> RenderedConfig:
        """根据结构化字段渲染期望配置内容。"""
        program_name = self.build_program_name(job_name, module_name)
        normalized_config_name = self.build_config_name(config_name, program_name)
        normalized_jar_name = ensure_safe_name(jar_name or self.build_default_jar_name(module_name), "jarName")
        ensure_valid_port(port)

        context: dict[str, Any] = {
            "program_name": program_name,
            "job_name": job_name,
            "module_name": module_name,
            "java_path": java_path,
            "active": active,
            "port": port,
            "jar_name": normalized_jar_name,
            "xms": xms,
            "xmx": xmx,
            "user": user,
            **FIXED_TEMPLATE_OPTIONS,
        }

        template = self.environment.get_template("supervisor_program.ini.j2")
        content = template.render(**context).strip() + "\n"
        self.validate_ini(content)
        return RenderedConfig(program_name=program_name, config_name=normalized_config_name, content=content)

    @staticmethod
    def validate_ini(content: str) -> None:
        """校验渲染后的内容为合法 INI。"""
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        try:
            parser.read_string(content)
        except configparser.Error as exc:
            raise ParamError(f"Supervisor 模板渲染结果非法: {exc}") from exc

        sections = parser.sections()
        if len(sections) != 1 or not sections[0].startswith("program:"):
            raise ParamError("Supervisor 配置必须包含且仅包含一个 [program:*] 段")

    @staticmethod
    def parse(content: str) -> ParsedConfig:
        """把配置文本反向解析为结构化字段。"""
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        try:
            parser.read_string(content)
        except configparser.Error as exc:
            raise ParamError(f"Supervisor 配置内容非法: {exc}") from exc

        sections = parser.sections()
        if len(sections) != 1 or not sections[0].startswith("program:"):
            raise ParamError("Supervisor 配置必须包含且仅包含一个 [program:*] 段")

        section_name = sections[0]
        program_name = section_name.split("program:", 1)[1]
        options = {key: value for key, value in parser.items(section_name)}
        command = options.get("command", "")
        directory = options.get("directory")
        job_name, module_name = _extract_job_and_module(directory)
        jar_path = _extract_text(JAR_PATTERN, command)

        java_path = command.split(" ", 1)[0] if command else None
        return ParsedConfig(
            program_name=program_name,
            options=options,
            job_name=job_name,
            module_name=module_name,
            port=_extract_int(PORT_PATTERN, content),
            java_path=java_path,
            active=_extract_text(PROFILE_PATTERN, command),
            jar_name=Path(jar_path).name if jar_path else None,
            xms=_extract_text(XMS_PATTERN, command),
            xmx=_extract_text(XMX_PATTERN, command),
            run_user=(options.get("user") or "").strip() or None,
        )


def _extract_text(pattern: re.Pattern[str], text: str) -> Optional[str]:
    match = pattern.search(text)
    if not match:
        return None
    return match.group("value")


def _extract_int(pattern: re.Pattern[str], text: str) -> Optional[int]:
    match = pattern.search(text)
    if not match:
        return None
    return int(match.group("port"))


def _extract_job_and_module(directory: str | None) -> tuple[Optional[str], Optional[str]]:
    """按固定目录约定反解业务作业与模块名。"""
    normalized = (directory or "").strip().strip("/")
    if not normalized:
        return None, None

    parts = normalized.split("/")
    if len(parts) != 4 or parts[0] != "data" or parts[1] != "content":
        return None, None
    return parts[2], parts[3]
