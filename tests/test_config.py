"""配置加载与环境文件测试。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import app.core.config as config_module


REPO_ROOT = Path(__file__).resolve().parents[1]


def _prepare_repo_files(tmp_path: Path) -> Path:
    repo_root = (tmp_path / "repo").resolve()
    repo_root.mkdir()

    (repo_root / "config.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  port: 18880",
                "database:",
                "  password: yaml#password",
                "auth:",
                "  jwtSecret: yaml-secret-0123456789abcdef",
                "supervisor:",
                f"  confDir: {repo_root / 'yaml-supervisord.d'}",
                "executor:",
                f"  inventoryPath: {repo_root / 'yaml-inventory'}",
            ]
        ),
        encoding="utf-8",
    )

    (repo_root / ".env.dev").write_text(
        "\n".join(
            [
                "APP_PORT=18881",
                "DATABASE_PASSWORD=dev#password",
                "JWT_SECRET=dev-secret-0123456789abcdef",
                f"SUPERVISOR_CONF_DIR={repo_root / 'dev-supervisord.d'}",
                f"ANSIBLE_INVENTORY_PATH={repo_root / 'dev-inventory'}",
            ]
        ),
        encoding="utf-8",
    )

    (repo_root / ".env.prod").write_text(
        "\n".join(
            [
                "APP_PORT=28881",
                "DATABASE_PASSWORD=prod#password",
                "JWT_SECRET=prod-secret-0123456789abcdef",
                f"SUPERVISOR_CONF_DIR={repo_root / 'prod-supervisord.d'}",
                f"ANSIBLE_INVENTORY_PATH={repo_root / 'prod-inventory'}",
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
    assert settings.database.password == "dev#password"
    assert settings.auth.jwt_secret == "dev-secret-0123456789abcdef"
    assert settings.supervisor.conf_dir == (repo_root / "dev-supervisord.d").resolve()


def test_load_settings_reads_prod_env_file(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings({"APP_ENV": "prod"})

    assert settings.app.port == 28881
    assert settings.database.password == "prod#password"
    assert settings.auth.jwt_secret == "prod-secret-0123456789abcdef"
    assert settings.executor.ansible_inventory_path == (repo_root / "prod-inventory").resolve()


def test_process_env_overrides_env_file(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings(
        {
            "APP_ENV": "dev",
            "APP_PORT": "38881",
            "DATABASE_PASSWORD": "explicit#password",
            "JWT_SECRET": "explicit-secret-0123456789abcdef",
        }
    )

    assert settings.app.port == 38881
    assert settings.database.password == "explicit#password"
    assert settings.auth.jwt_secret == "explicit-secret-0123456789abcdef"


def test_env_file_overrides_config_yaml(monkeypatch, tmp_path):
    repo_root = _prepare_repo_files(tmp_path)
    monkeypatch.setattr(config_module, "_repo_root", lambda: repo_root)

    settings = config_module.load_settings({"APP_ENV": "dev"})

    assert settings.database.password != "yaml#password"
    assert settings.database.password == "dev#password"


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


@pytest.mark.parametrize("app_env", ["dev", "prod"])
def test_run_script_passes_selected_env_to_python(tmp_path, app_env):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    capture_path = tmp_path / "captured_app_env.txt"
    args_path = tmp_path / "captured_args.txt"
    python_stub = stub_dir / "python3.12"
    python_stub.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "printf '%s' \"${APP_ENV:-}\" > \"$RUN_SH_CAPTURE_FILE\"",
                "printf '%s\\n' \"$@\" > \"$RUN_SH_ARGS_FILE\"",
            ]
        ),
        encoding="utf-8",
    )
    python_stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    env["RUN_SH_CAPTURE_FILE"] = str(capture_path)
    env["RUN_SH_ARGS_FILE"] = str(args_path)

    result = subprocess.run(
        ["bash", "scripts/run.sh", app_env],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert capture_path.read_text(encoding="utf-8") == app_env
    assert args_path.read_text(encoding="utf-8").strip() == "-"
