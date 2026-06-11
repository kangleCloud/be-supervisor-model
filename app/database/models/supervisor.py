"""Supervisor 相关 ORM 模型。"""
from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class SupervisorServiceModel(Model):
    """sys_supervisor_service。"""

    id = fields.BigIntField(pk=True)
    host_ip = fields.CharField(max_length=64)
    config_path = fields.CharField(max_length=500)
    file_name = fields.CharField(max_length=255)
    content_program_name = fields.CharField(max_length=255)
    manage_mode = fields.CharField(max_length=32, default="TEMPLATE_MANAGED")
    baseline_content = fields.TextField(null=True)
    metadata_complete = fields.BooleanField(default=True)
    parse_warnings = fields.TextField(null=True)
    job_name = fields.CharField(max_length=128, null=True)
    module_name = fields.CharField(max_length=128, null=True)
    java_path = fields.CharField(max_length=500, null=True)
    active_profile = fields.CharField(max_length=64, null=True)
    port = fields.IntField(null=True)
    jar_name = fields.CharField(max_length=255, null=True)
    xms = fields.CharField(max_length=32, null=True)
    xmx = fields.CharField(max_length=32, null=True)
    run_user = fields.CharField(max_length=64, null=True)
    status = fields.CharField(max_length=32, default="UNKNOWN")
    pid = fields.CharField(max_length=32, null=True)
    uptime = fields.CharField(max_length=64, null=True)
    status_sync_time = fields.DatetimeField(null=True)
    command = fields.CharField(max_length=2000, null=True)
    directory = fields.CharField(max_length=1000, null=True)
    stdout_logfile = fields.CharField(max_length=1000, null=True)
    has_backup = fields.BooleanField(default=False)
    config_content = fields.TextField(null=True)
    backup_config_content = fields.TextField(null=True)
    last_sync_at = fields.DatetimeField(null=True)
    sync_status = fields.CharField(max_length=16, default="UNKNOWN")
    sync_error = fields.CharField(max_length=1000, null=True)
    is_archived = fields.BooleanField(default=False)
    archived_at = fields.DatetimeField(null=True)
    restored_at = fields.DatetimeField(null=True)
    create_time = fields.DatetimeField(auto_now_add=True)
    update_time = fields.DatetimeField(auto_now=True)
    create_by_id = fields.BigIntField(null=True)
    create_by = fields.CharField(max_length=50, null=True)
    update_by_id = fields.BigIntField(null=True)
    update_by = fields.CharField(max_length=50, null=True)
    remark = fields.CharField(max_length=500, null=True)

    class Meta:
        table = "sys_supervisor_service"
        table_description = "Supervisor服务主数据表"
        unique_together = (("host_ip", "config_path"),)
        indexes = (
            ("host_ip", "content_program_name"),
            ("host_ip", "manage_mode"),
            ("host_ip", "is_archived"),
            ("host_ip", "status"),
        )


class SupervisorImportStagingModel(Model):
    """sys_supervisor_import_staging。"""

    id = fields.BigIntField(pk=True)
    batch_id = fields.CharField(max_length=36)
    host_ip = fields.CharField(max_length=64)
    operator_id = fields.BigIntField()
    operator_name = fields.CharField(max_length=50)
    config_path = fields.CharField(max_length=500)
    file_name = fields.CharField(max_length=255)
    content_program_name = fields.CharField(max_length=255, null=True)
    baseline_content = fields.TextField(null=True)
    metadata_complete = fields.BooleanField(default=True)
    parse_warnings = fields.TextField(null=True)
    job_name = fields.CharField(max_length=128, null=True)
    module_name = fields.CharField(max_length=128, null=True)
    java_path = fields.CharField(max_length=500, null=True)
    active_profile = fields.CharField(max_length=64, null=True)
    port = fields.IntField(null=True)
    jar_name = fields.CharField(max_length=255, null=True)
    xms = fields.CharField(max_length=32, null=True)
    xmx = fields.CharField(max_length=32, null=True)
    run_user = fields.CharField(max_length=64, null=True)
    result = fields.CharField(max_length=16)
    message = fields.CharField(max_length=500, null=True)
    create_time = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "sys_supervisor_import_staging"
        table_description = "Supervisor导入预检暂存表"
        indexes = (("batch_id",), ("host_ip",))
