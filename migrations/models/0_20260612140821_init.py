from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS `sys_login_log` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `tenant_id` BIGINT,
    `user_id` BIGINT,
    `token_id` BIGINT,
    `user_name` VARCHAR(50) NOT NULL DEFAULT '',
    `ipaddr` VARCHAR(128) NOT NULL DEFAULT '',
    `login_location` VARCHAR(255) NOT NULL DEFAULT '',
    `browser` VARCHAR(50) NOT NULL DEFAULT '',
    `os` VARCHAR(50) NOT NULL DEFAULT '',
    `status` VARCHAR(1) NOT NULL DEFAULT '0',
    `msg` VARCHAR(255) NOT NULL DEFAULT '',
    `token_jti` VARCHAR(64),
    `login_time` DATETIME(6),
    `create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `is_deleted` INT NOT NULL DEFAULT 0,
    `create_by_id` BIGINT,
    `create_by` VARCHAR(50),
    `update_by_id` BIGINT,
    `update_by` VARCHAR(50),
    `version` BIGINT NOT NULL DEFAULT 0,
    `remark` VARCHAR(500),
    KEY `idx_sys_login_l_status_1b17d4` (`status`),
    KEY `idx_sys_login_l_login_t_1b0e5c` (`login_time`),
    KEY `idx_sys_login_l_user_id_e3df6d` (`user_id`),
    KEY `idx_sys_login_l_token_i_50a7a9` (`token_id`)
) CHARACTER SET utf8mb4 COMMENT='登录审计日志表';
CREATE TABLE IF NOT EXISTS `sys_login_token` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `tenant_id` BIGINT,
    `user_id` BIGINT NOT NULL,
    `user_name` VARCHAR(50) NOT NULL,
    `token_jti` VARCHAR(64) NOT NULL UNIQUE,
    `token_digest` VARCHAR(64) NOT NULL,
    `login_ip` VARCHAR(128) NOT NULL DEFAULT '',
    `user_agent` VARCHAR(500) NOT NULL DEFAULT '',
    `issued_at` DATETIME(6) NOT NULL,
    `expires_at` DATETIME(6) NOT NULL,
    `revoked_time` DATETIME(6),
    `create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `is_deleted` INT NOT NULL DEFAULT 0,
    `create_by_id` BIGINT,
    `create_by` VARCHAR(50),
    `update_by_id` BIGINT,
    `update_by` VARCHAR(50),
    `version` BIGINT NOT NULL DEFAULT 0,
    `remark` VARCHAR(500),
    KEY `idx_sys_login_t_user_id_8b90f4` (`user_id`),
    KEY `idx_sys_login_t_expires_a04bc9` (`expires_at`)
) CHARACTER SET utf8mb4 COMMENT='JWT登录令牌表';
CREATE TABLE IF NOT EXISTS `sys_supervisor_import_staging` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `batch_id` VARCHAR(36) NOT NULL,
    `host_ip` VARCHAR(64) NOT NULL,
    `operator_id` BIGINT NOT NULL,
    `operator_name` VARCHAR(50) NOT NULL,
    `config_path` VARCHAR(500) NOT NULL,
    `file_name` VARCHAR(255) NOT NULL,
    `content_program_name` VARCHAR(255),
    `baseline_content` LONGTEXT,
    `metadata_complete` BOOL NOT NULL DEFAULT 1,
    `parse_warnings` LONGTEXT,
    `job_name` VARCHAR(128),
    `module_name` VARCHAR(128),
    `java_path` VARCHAR(500),
    `active_profile` VARCHAR(64),
    `port` INT,
    `jar_name` VARCHAR(255),
    `xms` VARCHAR(32),
    `xmx` VARCHAR(32),
    `run_user` VARCHAR(64),
    `result` VARCHAR(16) NOT NULL,
    `message` VARCHAR(500),
    `create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY `idx_sys_supervi_batch_i_29288b` (`batch_id`),
    KEY `idx_sys_supervi_host_ip_a4c948` (`host_ip`)
) CHARACTER SET utf8mb4 COMMENT='Supervisor导入预检暂存表';
CREATE TABLE IF NOT EXISTS `sys_supervisor_service` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `host_ip` VARCHAR(64) NOT NULL,
    `config_path` VARCHAR(500) NOT NULL,
    `file_name` VARCHAR(255) NOT NULL,
    `content_program_name` VARCHAR(255) NOT NULL,
    `manage_mode` VARCHAR(32) NOT NULL DEFAULT 'TEMPLATE_MANAGED',
    `baseline_content` LONGTEXT,
    `metadata_complete` BOOL NOT NULL DEFAULT 1,
    `parse_warnings` LONGTEXT,
    `job_name` VARCHAR(128),
    `module_name` VARCHAR(128),
    `java_path` VARCHAR(500),
    `active_profile` VARCHAR(64),
    `port` INT,
    `jar_name` VARCHAR(255),
    `xms` VARCHAR(32),
    `xmx` VARCHAR(32),
    `run_user` VARCHAR(64),
    `status` VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    `pid` VARCHAR(32),
    `uptime` VARCHAR(64),
    `status_sync_time` DATETIME(6),
    `command` VARCHAR(2000),
    `directory` VARCHAR(1000),
    `stdout_logfile` VARCHAR(1000),
    `has_backup` BOOL NOT NULL DEFAULT 0,
    `config_content` LONGTEXT,
    `backup_config_content` LONGTEXT,
    `last_sync_at` DATETIME(6),
    `sync_status` VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN',
    `sync_error` VARCHAR(1000),
    `is_archived` BOOL NOT NULL DEFAULT 0,
    `archived_at` DATETIME(6),
    `restored_at` DATETIME(6),
    `create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `create_by_id` BIGINT,
    `create_by` VARCHAR(50),
    `update_by_id` BIGINT,
    `update_by` VARCHAR(50),
    `remark` VARCHAR(500),
    UNIQUE KEY `uid_sys_supervi_host_ip_fd56b1` (`host_ip`, `config_path`),
    KEY `idx_sys_supervi_host_ip_fd333e` (`host_ip`, `content_program_name`),
    KEY `idx_sys_supervi_host_ip_fc48fe` (`host_ip`, `manage_mode`),
    KEY `idx_sys_supervi_host_ip_792501` (`host_ip`, `is_archived`),
    KEY `idx_sys_supervi_host_ip_ff9687` (`host_ip`, `status`)
) CHARACTER SET utf8mb4 COMMENT='Supervisor服务主数据表';
CREATE TABLE IF NOT EXISTS `sys_user` (
    `id` BIGINT NOT NULL PRIMARY KEY,
    `tenant_id` BIGINT,
    `user_name` VARCHAR(50) NOT NULL UNIQUE,
    `nick_name` VARCHAR(50),
    `password` VARCHAR(255) NOT NULL,
    `status` INT NOT NULL DEFAULT 1,
    `is_super_admin` INT NOT NULL DEFAULT 0,
    `login_time` DATETIME(6),
    `login_address` VARCHAR(128),
    `pwd_update_date` DATETIME(6),
    `create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `is_deleted` INT NOT NULL DEFAULT 0,
    `create_by_id` BIGINT,
    `create_by` VARCHAR(50),
    `update_by_id` BIGINT,
    `update_by` VARCHAR(50),
    `version` BIGINT NOT NULL DEFAULT 0,
    `remark` VARCHAR(500),
    KEY `idx_sys_user_user_na_8d52f2` (`user_name`)
) CHARACTER SET utf8mb4 COMMENT='用户信息表';
CREATE TABLE IF NOT EXISTS `aerich` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `version` VARCHAR(255) NOT NULL,
    `app` VARCHAR(100) NOT NULL,
    `content` JSON NOT NULL
) CHARACTER SET utf8mb4;
INSERT INTO `sys_user`(
    `id`, `tenant_id`, `user_name`, `nick_name`, `password`, `status`, `is_super_admin`,
    `pwd_update_date`, `create_by_id`, `create_by`, `update_by_id`, `update_by`, `version`, `remark`
)
SELECT
    next_user.next_id,
    0,
    'admin',
    '超级管理员',
    '$2b$12$27nxsNqi/PQ8Yo3Py.cs/uWDVi.e1z7lQQhMbmm5AIEjhNRWodN7K',
    1,
    1,
    CURRENT_TIMESTAMP,
    0,
    'system',
    0,
    'system',
    0,
    '系统初始化超级管理员，请尽快重置默认密码'
FROM (
    SELECT COALESCE(MAX(`id`), 0) + 1 AS next_id
    FROM `sys_user`
) AS next_user
WHERE NOT EXISTS (
    SELECT 1
    FROM `sys_user`
    WHERE `user_name` = 'admin'
      AND `is_deleted` = 0
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
    "8Vl2ndlpehbPD3B+6MfL1/8BcHwotQ=="
)
