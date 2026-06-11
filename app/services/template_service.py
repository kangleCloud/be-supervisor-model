"""Supervisor 模板渲染与解析。"""
from __future__ import annotations

import configparser
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.core.config import Settings
from app.core.exceptions import ParamError
from app.core.security import ensure_safe_name, ensure_safe_program_name, ensure_valid_port, normalize_config_name
from app.schemas.supervisor import ServiceCreateRequest


PORT_PATTERN = re.compile(r"(?:-Dserver\.port=|server\.port=|port=)(?P<port>\d+)")
PROFILE_PATTERN = re.compile(r"-Dspring\.profiles\.active=(?P<value>\S+)")
XMS_PATTERN = re.compile(r"-Xms(?P<value>\S+)")
XMX_PATTERN = re.compile(r"-Xmx(?P<value>\S+)")
JAR_PATTERN = re.compile(r"(?P<value>/\S+\.jar)")
SECTION_PATTERN = re.compile(r"^\[(?P<section>[^\]]+)\]\s*$")
OPTION_PATTERN = re.compile(r"^(?P<key>[^=:#;\s][^=:]*?)\s*(?:=|:)")

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
    metadata_complete: bool
    warnings: tuple[str, ...]


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
            config_name=payload.file_name,
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

    @classmethod
    def ensure_program_identity(
        cls,
        *,
        job_name: str | None,
        module_name: str | None,
        content_program_name: str,
    ) -> str:
        """校验 contentProgramName 是否与模板规则生成的 program_name 一致。"""
        if not job_name or not module_name:
            raise ParamError("无法根据 jobName/moduleName 验证 contentProgramName")
        expected_program_name = cls.build_program_name(job_name, module_name)
        safe_content_program_name = ensure_safe_program_name(content_program_name)
        if safe_content_program_name != expected_program_name:
            raise ParamError(
                f"contentProgramName 与模板 program_name 不一致: expected={expected_program_name}, actual={safe_content_program_name}"
            )
        return expected_program_name

    @staticmethod
    def parse(content: str) -> ParsedConfig:
        """把配置文本反向解析为结构化字段。"""
        # 现场导入允许 legacy 文件存在重复 key，解析阶段要尽量保留快照，而不是因为格式旧就整文件丢弃。
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        try:
            parser.read_string(content)
        except configparser.Error as exc:
            raise ParamError(f"Supervisor 配置内容非法: {exc}") from exc

        sections = parser.sections()
        program_sections = [section for section in sections if section.strip().lower().startswith("program:")]
        if len(program_sections) != 1:
            raise ParamError("Supervisor 配置必须包含且仅包含一个合法的 [program:*] 段")

        warnings = list(_collect_duplicate_option_warnings(content))
        if len(sections) != 1:
            warnings.append("存在额外 section，已仅按 [program:*] 段解析")

        section_name = program_sections[0]
        program_name = ensure_safe_program_name(section_name.split("program:", 1)[1].strip())
        options = {key: value for key, value in parser.items(section_name)}
        command = options.get("command", "")
        directory = options.get("directory")
        jar_path = _extract_text(JAR_PATTERN, command)
        job_name, module_name = _extract_job_and_module(directory, jar_path, program_name)
        port = _extract_int(PORT_PATTERN, content)
        java_path = _extract_java_path(command)
        active = _extract_text(PROFILE_PATTERN, command)
        jar_name = Path(jar_path).name if jar_path else None
        xms = _extract_text(XMS_PATTERN, command)
        xmx = _extract_text(XMX_PATTERN, command)
        run_user = (options.get("user") or "").strip() or None

        return ParsedConfig(
            program_name=program_name,
            options=options,
            job_name=job_name,
            module_name=module_name,
            port=port,
            java_path=java_path,
            active=active,
            jar_name=jar_name,
            xms=xms,
            xmx=xmx,
            run_user=run_user,
            metadata_complete=_is_metadata_complete(
                job_name=job_name,
                module_name=module_name,
                port=port,
                java_path=java_path,
                active=active,
                jar_name=jar_name,
                xms=xms,
                xmx=xmx,
                run_user=run_user,
            ),
            warnings=tuple(dict.fromkeys(warnings)),
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


def _extract_java_path(command: str) -> Optional[str]:
    if not command.strip():
        return None
    try:
        return shlex.split(command)[0]
    except ValueError:
        # legacy 命令可能存在未闭合引号，退回最保守的首 token 解析。
        return command.split(" ", 1)[0].strip() or None


def _collect_duplicate_option_warnings(content: str) -> tuple[str, ...]:
    """扫描重复 key，保持“最后一个值生效”同时返回中文告警。"""
    warnings: list[str] = []
    current_section: str | None = None
    seen_keys: dict[str, set[str]] = {}
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        section_match = SECTION_PATTERN.match(stripped)
        if section_match is not None:
            current_section = section_match.group("section").strip()
            seen_keys.setdefault(current_section, set())
            continue
        if current_section is None:
            continue
        option_match = OPTION_PATTERN.match(stripped)
        if option_match is None:
            continue
        key = option_match.group("key").strip()
        current_seen = seen_keys.setdefault(current_section, set())
        if key in current_seen:
            warnings.append(f"section[{current_section}] 存在重复 key: {key}，已按最后一个值生效")
            continue
        current_seen.add(key)
    return tuple(warnings)


def _extract_job_and_module(
    directory: str | None,
    jar_path: str | None,
    program_name: str,
) -> tuple[Optional[str], Optional[str]]:
    """优先按目录，其次按 Jar 路径，最后按 programName 兜底反解。"""
    directory_job, directory_module = _extract_job_and_module_from_directory(directory)
    jar_job, jar_module = _extract_job_and_module_from_jar(jar_path)
    program_job, program_module = _extract_job_and_module_from_program(program_name)
    return (
        _first_non_empty(directory_job, jar_job, program_job),
        _first_non_empty(directory_module, jar_module, program_module),
    )


def _extract_job_and_module_from_directory(directory: str | None) -> tuple[Optional[str], Optional[str]]:
    parts = _extract_path_after_content(directory)
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def _extract_job_and_module_from_jar(jar_path: str | None) -> tuple[Optional[str], Optional[str]]:
    parts = _extract_path_after_content(jar_path)
    if not parts:
        return None, None
    jar_name = Path(parts[-1]).stem if parts[-1].endswith(".jar") else None
    if len(parts) == 1:
        return None, jar_name
    if len(parts) == 2:
        return parts[0], jar_name
    return parts[0], parts[1]


def _extract_job_and_module_from_program(program_name: str) -> tuple[Optional[str], Optional[str]]:
    if "_" not in program_name:
        return None, None
    job_name, module_name = program_name.rsplit("_", 1)
    return job_name or None, module_name or None


def _extract_path_after_content(path_value: str | None) -> list[str]:
    normalized = (path_value or "").strip()
    if not normalized:
        return []
    parts = [part for part in normalized.replace("\\", "/").split("/") if part]
    if len(parts) < 3 or parts[0] != "data" or parts[1] != "content":
        return []
    return parts[2:]


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


def _is_metadata_complete(
    *,
    job_name: Optional[str],
    module_name: Optional[str],
    port: Optional[int],
    java_path: Optional[str],
    active: Optional[str],
    jar_name: Optional[str],
    xms: Optional[str],
    xmx: Optional[str],
    run_user: Optional[str],
) -> bool:
    values: tuple[object | None, ...] = (
        job_name,
        module_name,
        port,
        java_path,
        active,
        jar_name,
        xms,
        xmx,
        run_user,
    )
    return all(value not in (None, "") for value in values)
