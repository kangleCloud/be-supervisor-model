"""Supervisor 主机实时概况服务。"""
from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

from app.core.config import Settings
from app.executor.base import CommandResult
from app.services.host_service import HostService


CONNECTION_STATE_CONNECTED = "CONNECTED"
CONNECTION_STATE_UNREACHABLE = "UNREACHABLE"
CONNECTION_STATE_UNSUPPORTED = "UNSUPPORTED"

_UNREACHABLE_MARKERS = (
    "unreachable",
    "failed to connect",
    "connection timed out",
    "connection refused",
    "permission denied",
    "host key verification failed",
    "could not resolve hostname",
    "authentication failed",
    "ansible 命令执行超时",
    "ssh",
)


class SupervisorOverviewService:
    """采集主机实时概况，不落库。"""

    def __init__(self, settings: Settings, host_service: HostService):
        self.settings = settings
        self.host_service = host_service

    def get_overview(self, host: str) -> dict[str, object]:
        host_config = self.host_service.get_host(host)
        collected_at = self._now_text()
        fallback_host_name = host_config.name

        if host_config.executor_type == "local":
            return self._build_response(
                host=host_config.ip,
                host_name=fallback_host_name,
                executor_type=host_config.executor_type,
                available=False,
                connection_state=CONNECTION_STATE_UNSUPPORTED,
                collected_at=collected_at,
                cpu_usage_percent=0.0,
                memory_used_bytes=0,
                memory_total_bytes=0,
                memory_usage_percent=0.0,
                supervisorctl_available=False,
                conf_dir_readable=False,
                warnings=["local 执行器暂不支持服务器概况实时采集，仅支持远端 Linux 主机"],
            )

        executor = self.host_service.get_executor(host_config.ip)
        # 概况采集与其他 ansible 远端读取统一复用执行器超时配置，避免单独维护另一套 8s 特例。
        result = executor.run_command(
            ["sh", "-lc", self._build_overview_script()],
            timeout=self.settings.executor.ansible_timeout_seconds,
        )
        if not result.success:
            return self._build_response(
                host=host_config.ip,
                host_name=fallback_host_name,
                executor_type=host_config.executor_type,
                available=False,
                connection_state=CONNECTION_STATE_UNREACHABLE,
                collected_at=collected_at,
                cpu_usage_percent=0.0,
                memory_used_bytes=0,
                memory_total_bytes=0,
                memory_usage_percent=0.0,
                supervisorctl_available=False,
                conf_dir_readable=False,
                warnings=[self._normalize_failure_message(result)],
            )

        parsed = self._parse_output(result.stdout)
        warnings = list(parsed["warnings"])
        host_name = str(parsed["host_name"] or fallback_host_name)
        supervisorctl_available = bool(parsed["supervisorctl_available"])
        conf_dir_readable = bool(parsed["conf_dir_readable"])

        if not supervisorctl_available:
            warnings.append("目标主机未发现 supervisorctl 命令")
        if not conf_dir_readable:
            warnings.append(f"目标主机配置目录不可读: {self.settings.supervisor.conf_dir}")

        status = str(parsed["status"])
        available = status == "OK" and supervisorctl_available and conf_dir_readable
        connection_state = CONNECTION_STATE_CONNECTED if status == "OK" else CONNECTION_STATE_UNSUPPORTED

        return self._build_response(
            host=host_config.ip,
            host_name=host_name,
            executor_type=host_config.executor_type,
            available=available,
            connection_state=connection_state,
            collected_at=collected_at,
            cpu_usage_percent=float(parsed["cpu_usage_percent"]),
            memory_used_bytes=int(parsed["memory_used_bytes"]),
            memory_total_bytes=int(parsed["memory_total_bytes"]),
            memory_usage_percent=float(parsed["memory_usage_percent"]),
            supervisorctl_available=supervisorctl_available,
            conf_dir_readable=conf_dir_readable,
            warnings=warnings,
        )

    def _build_overview_script(self) -> str:
        conf_dir = PurePosixPath(str(self.settings.supervisor.conf_dir))
        return f"""
conf_dir={self._shell_quote(str(conf_dir))}
status=OK
hostname_value=$(hostname 2>/dev/null || echo unknown)
supervisorctl_available=false
conf_dir_readable=false
cpu_usage_percent=0.00
mem_total_bytes=0
mem_used_bytes=0
mem_usage_percent=0.00

if command -v supervisorctl >/dev/null 2>&1; then
  supervisorctl_available=true
fi

if [ -d "$conf_dir" ] && [ -r "$conf_dir" ]; then
  conf_dir_readable=true
fi

if [ ! -r /proc/stat ] || [ ! -r /proc/meminfo ]; then
  status=UNSUPPORTED
  echo "WARNING=/proc 文件不可读，无法采集 Linux CPU/内存概况"
else
  cpu_line_1=$(awk '/^cpu / {{print $2" "$3" "$4" "$5" "$6" "$7" "$8" "$9}}' /proc/stat 2>/dev/null)
  sleep 0.2
  cpu_line_2=$(awk '/^cpu / {{print $2" "$3" "$4" "$5" "$6" "$7" "$8" "$9}}' /proc/stat 2>/dev/null)
  mem_values=$(awk '/^MemTotal:/ {{total=$2}} /^MemAvailable:/ {{available=$2}} END {{if (total == "" || available == "") exit 1; print total" "available}}' /proc/meminfo 2>/dev/null)

  if [ -z "$cpu_line_1" ] || [ -z "$cpu_line_2" ] || [ -z "$mem_values" ]; then
    status=UNSUPPORTED
    echo "WARNING=/proc 数据不完整，无法生成可信的 CPU/内存概况"
  else
    cpu_usage_percent=$(awk -v line1="$cpu_line_1" -v line2="$cpu_line_2" 'BEGIN {{
      split(line1, a, " ");
      split(line2, b, " ");
      total1=0; total2=0;
      for (i=1; i<=8; i++) {{ total1 += a[i]; total2 += b[i]; }}
      idle1=a[4] + a[5];
      idle2=b[4] + b[5];
      dt=total2-total1;
      di=idle2-idle1;
      if (dt <= 0) {{ printf "0.00"; }}
      else {{ printf "%.2f", ((dt-di) * 100.0) / dt; }}
    }}')

    mem_total_kb=$(printf '%s\n' "$mem_values" | awk '{{print $1}}')
    mem_available_kb=$(printf '%s\n' "$mem_values" | awk '{{print $2}}')
    if [ -z "$mem_total_kb" ] || [ -z "$mem_available_kb" ]; then
      status=UNSUPPORTED
      echo "WARNING=内存信息解析失败，无法生成可信概况"
    else
      mem_total_bytes=$((mem_total_kb * 1024))
      mem_used_bytes=$(((mem_total_kb - mem_available_kb) * 1024))
      if [ "$mem_total_bytes" -le 0 ]; then
        mem_usage_percent=0.00
        status=UNSUPPORTED
        echo "WARNING=内存总量异常，无法生成可信概况"
      else
        mem_usage_percent=$(awk -v used="$mem_used_bytes" -v total="$mem_total_bytes" 'BEGIN {{ printf "%.2f", (used * 100.0) / total }}')
      fi
    fi
  fi
fi

echo "STATUS=$status"
echo "HOSTNAME=$hostname_value"
echo "SUPERVISORCTL_AVAILABLE=$supervisorctl_available"
echo "CONF_DIR_READABLE=$conf_dir_readable"
echo "CPU_USAGE_PERCENT=$cpu_usage_percent"
echo "MEM_TOTAL_BYTES=$mem_total_bytes"
echo "MEM_USED_BYTES=$mem_used_bytes"
echo "MEM_USAGE_PERCENT=$mem_usage_percent"
""".strip()

    @staticmethod
    def _shell_quote(text: str) -> str:
        return "'" + text.replace("'", "'\"'\"'") + "'"

    def _parse_output(self, stdout: str) -> dict[str, object]:
        values: dict[str, str] = {}
        warnings: list[str] = []
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key == "WARNING":
                if value:
                    warnings.append(value)
                continue
            values[key] = value.strip()

        status = values.get("STATUS", "UNSUPPORTED").upper()
        if status not in {"OK", "UNSUPPORTED"}:
            warnings.append(f"未知概况采集状态: {status}")
            status = "UNSUPPORTED"

        parsed: dict[str, object] = {
            "status": status,
            "host_name": values.get("HOSTNAME", ""),
            "supervisorctl_available": self._parse_bool(values.get("SUPERVISORCTL_AVAILABLE")),
            "conf_dir_readable": self._parse_bool(values.get("CONF_DIR_READABLE")),
            "cpu_usage_percent": 0.0,
            "memory_total_bytes": 0,
            "memory_used_bytes": 0,
            "memory_usage_percent": 0.0,
            "warnings": warnings,
        }

        if status != "OK":
            return parsed

        try:
            parsed["cpu_usage_percent"] = self._clamp_percent(float(values["CPU_USAGE_PERCENT"]))
            parsed["memory_total_bytes"] = max(0, int(values["MEM_TOTAL_BYTES"]))
            parsed["memory_used_bytes"] = max(0, int(values["MEM_USED_BYTES"]))
            parsed["memory_usage_percent"] = self._clamp_percent(float(values["MEM_USAGE_PERCENT"]))
        except (KeyError, TypeError, ValueError):
            warnings.append("概况采集结果解析失败，返回不支持采集状态")
            parsed["status"] = "UNSUPPORTED"
            parsed["cpu_usage_percent"] = 0.0
            parsed["memory_total_bytes"] = 0
            parsed["memory_used_bytes"] = 0
            parsed["memory_usage_percent"] = 0.0
            return parsed

        if int(parsed["memory_total_bytes"]) <= 0:
            warnings.append("内存总量无效，返回不支持采集状态")
            parsed["status"] = "UNSUPPORTED"
            parsed["memory_total_bytes"] = 0
            parsed["memory_used_bytes"] = 0
            parsed["memory_usage_percent"] = 0.0

        return parsed

    @staticmethod
    def _parse_bool(value: str | None) -> bool:
        return (value or "").strip().lower() == "true"

    @staticmethod
    def _clamp_percent(value: float) -> float:
        return round(min(100.0, max(0.0, value)), 2)

    def _build_response(
        self,
        *,
        host: str,
        host_name: str,
        executor_type: str,
        available: bool,
        connection_state: str,
        collected_at: str,
        cpu_usage_percent: float,
        memory_used_bytes: int,
        memory_total_bytes: int,
        memory_usage_percent: float,
        supervisorctl_available: bool,
        conf_dir_readable: bool,
        warnings: list[str],
    ) -> dict[str, object]:
        return {
            "host": host,
            "hostName": host_name,
            "executorType": executor_type,
            "available": available,
            "connectionState": connection_state,
            "collectedAt": collected_at,
            "cpu": {
                "usagePercent": round(cpu_usage_percent, 2),
            },
            "memory": {
                "usagePercent": round(memory_usage_percent, 2),
                "usedBytes": memory_used_bytes,
                "totalBytes": memory_total_bytes,
                "usedText": self._format_bytes(memory_used_bytes),
                "totalText": self._format_bytes(memory_total_bytes),
            },
            "checks": {
                "supervisorctlAvailable": supervisorctl_available,
                "confDirReadable": conf_dir_readable,
            },
            "warnings": warnings,
        }

    @staticmethod
    def _normalize_failure_message(result: CommandResult) -> str:
        message = result.stderr.strip() or result.stdout.strip() or "目标主机不可达"
        if "Ansible inventory 不存在或不可读" in message:
            return message
        normalized = message.lower()
        if any(marker in normalized for marker in _UNREACHABLE_MARKERS):
            return f"目标主机不可达: {message}"
        return f"概况采集失败: {message}"

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value <= 0:
            return "0 B"
        units = ("B", "KB", "MB", "GB", "TB")
        size = float(value)
        unit_index = 0
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"
        return f"{size:.2f} {units[unit_index]}"

    @staticmethod
    def _now_text() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
