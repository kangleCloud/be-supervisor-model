"""配置加载与环境文件测试。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import app.core.config as config_module


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _prepare_repo_files(tmp_path: Path) -> Path:
    repo_root = (tmp_path / "repo").resolve()
    repo_root.mkdir()

    (repo_root / "config.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  host: 0.0.0.0",
                "  port: 18881",
                "  logLevel: info",
                f"  logPath: {repo_root / 'logs' / 'app.log'}",
                "database:",
                "  host: 106.54.205.33",
                "  port: 3306",
                "  name: be_supervisor_model",
                "  user: root",
                "  connectTimeoutSeconds: 5",
                "auth:",
                "  accessTokenExpireMinutes: 480",
                "supervisor:",
                f"  confDir: {repo_root / 'shared-supervisord.d'}",
                "  commandTimeoutSeconds: 300",
                "executor:",
                "  type: local",
                f"  inventoryPath: {repo_root / 'shared-inventory'}",
                "  remoteUser: root",
                "  timeoutSeconds: 300",
            ]
        ),
        encoding="utf-8",
    )

    (repo_root / ".env.dev").write_text(
        "\n".join(
            [
                "DATABASE_PASSWORD=dev#password",
                "JWT_SECRET=dev-secret-0123456789abcdef",
                "APP_CONFIG_PATH=",
            ]
        ),
        encoding="utf-8",
    )

    (repo_root / ".env.prod").write_text(
        "\n".join(
            [
                "DATABASE_PASSWORD=prod#password",
                "JWT_SECRET=prod-secret-0123456789abcdef",
                "APP_CONFIG_PATH=",
            ]
        ),
        encoding="utf-8",
    )
    return repo_root


def test_load_settings_reads_dev_env_file(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings({"APP_ENV": "dev"})

    assert settings.app.port == 18881
    assert settings.app.log_path == (repo_root / "logs" / "app.log").resolve()
    assert settings.database.host == "106.54.205.33"
    assert settings.database.password == "dev#password"
    assert settings.auth.jwt_secret == "dev-secret-0123456789abcdef"
    assert settings.supervisor.conf_dir == (repo_root / "shared-supervisord.d").resolve()
    assert settings.supervisor.command_timeout_seconds == 300
    assert settings.executor.ansible_timeout_seconds == 300


def test_load_settings_reads_prod_env_file(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings({"APP_ENV": "prod"})

    assert settings.app.port == 18881
    assert settings.database.password == "prod#password"
    assert settings.auth.jwt_secret == "prod-secret-0123456789abcdef"
    assert settings.executor.ansible_inventory_path == (repo_root / "shared-inventory").resolve()


def test_process_env_overrides_env_file(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings(
        {
            "APP_ENV": "dev",
            "APP_PORT": "38881",
            "APP_LOG_PATH": str((repo_root / "override-logs" / "runtime.log").resolve()),
            "DATABASE_PASSWORD": "explicit#password",
            "JWT_SECRET": "explicit-secret-0123456789abcdef",
        }
    )

    assert settings.app.port == 38881
    assert settings.app.log_path == (repo_root / "override-logs" / "runtime.log").resolve()
    assert settings.database.password == "explicit#password"
    assert settings.auth.jwt_secret == "explicit-secret-0123456789abcdef"


def test_env_file_supplies_secrets_when_config_yaml_omits_them(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings({"APP_ENV": "dev"})

    assert settings.database.password == "dev#password"
    assert settings.auth.jwt_secret == "dev-secret-0123456789abcdef"


def test_load_settings_rejects_invalid_app_env(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    with pytest.raises(ValueError, match="APP_ENV 只支持 dev 或 prod"):
        config_module.load_settings({"APP_ENV": "staging"})


def test_load_settings_rejects_missing_app_env_file(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    missing_env_path = (tmp_path / "missing.env").resolve()
    with pytest.raises(ValueError, match="APP_ENV_FILE 指定的文件不存在"):
        config_module.load_settings({"APP_ENV_FILE": str(missing_env_path)})


def test_app_env_file_takes_precedence_over_app_env(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    custom_env_path = (tmp_path / "custom.env").resolve()
    custom_env_path.write_text(
        "\n".join(
            [
                "APP_PORT=48881",
                "DATABASE_PASSWORD=custom#password",
                "JWT_SECRET=custom-secret-0123456789abcdef",
                f"SUPERVISOR_CONF_DIR={repo_root / 'custom-supervisord.d'}",
                f"ANSIBLE_INVENTORY_PATH={repo_root / 'custom-inventory'}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings({"APP_ENV": "prod", "APP_ENV_FILE": str(custom_env_path)})

    assert settings.app.port == 48881
    assert settings.database.password == "custom#password"
    assert settings.auth.jwt_secret == "custom-secret-0123456789abcdef"


def test_load_settings_preserves_literal_etc_path(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    (repo_root / ".env.dev").write_text(
        "\n".join(
            [
                "DATABASE_PASSWORD=dev#password",
                "JWT_SECRET=dev-secret-0123456789abcdef",
                "SUPERVISOR_CONF_DIR=/etc/supervisord.d",
                "ANSIBLE_INVENTORY_PATH=/etc/ansible/supervisor_host",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings({"APP_ENV": "dev"})

    assert settings.supervisor.conf_dir == Path("/etc/supervisord.d")
    assert settings.executor.ansible_inventory_path == Path("/etc/ansible/supervisor_host")


def test_load_settings_without_app_env_reads_config_yaml_and_requires_process_secret(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    with pytest.raises(ValueError, match="JWT_SECRET 不能为空"):
        config_module.load_settings({})


def test_load_settings_allows_empty_log_path(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    config_text = (repo_root / "config.yaml").read_text(encoding="utf-8").replace(
        f"  logPath: {repo_root / 'logs' / 'app.log'}",
        "  logPath: ''",
    )
    (repo_root / "config.yaml").write_text(config_text, encoding="utf-8")
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings({"APP_ENV": "dev"})

    assert settings.app.log_path is None


def test_run_script_requires_env_argument():
    result = subprocess.run(
        ["bash", "scripts/run.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "用法: ./scripts/run.sh <dev|prod>" in result.stderr


@pytest.mark.parametrize(
    ("script_cwd", "command_prefix"),
    [
        (REPO_ROOT, ["bash", "scripts/run.sh"]),
        (SCRIPTS_DIR, ["bash", "run.sh"]),
    ],
)
@pytest.mark.parametrize("app_env", ["dev", "prod"])
def test_run_script_passes_selected_env_to_python(tmp_path, script_cwd, command_prefix, app_env):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    capture_path = tmp_path / "captured_app_env.txt"
    args_path = tmp_path / "captured_args.txt"
    pwd_path = tmp_path / "captured_pwd.txt"
    python_stub = stub_dir / "python3.12"
    python_stub.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "printf '%s' \"${APP_ENV:-}\" > \"$RUN_SH_CAPTURE_FILE\"",
                "printf '%s\\n' \"$@\" > \"$RUN_SH_ARGS_FILE\"",
                "pwd > \"$RUN_SH_PWD_FILE\"",
            ]
        ),
        encoding="utf-8",
    )
    python_stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    env["RUN_SH_CAPTURE_FILE"] = str(capture_path)
    env["RUN_SH_ARGS_FILE"] = str(args_path)
    env["RUN_SH_PWD_FILE"] = str(pwd_path)

    result = subprocess.run(
        [*command_prefix, app_env],
        cwd=script_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert capture_path.read_text(encoding="utf-8") == app_env
    assert args_path.read_text(encoding="utf-8").strip() == "-"
    assert pwd_path.read_text(encoding="utf-8").strip() == str(REPO_ROOT)
