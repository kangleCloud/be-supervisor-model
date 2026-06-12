-- Historical SQL snapshot only.
-- This file is retained for reference and manual comparison.
-- Runtime startup no longer executes this file; schema changes are managed by Aerich migrations under migrations/models/.

CREATE TABLE IF NOT EXISTS `sys_user` (
    `id` BIGINT NOT NULL PRIMARY KEY COMMENT 'ID',
    `tenant_id` BIGINT DEFAULT NULL COMMENT '租户ID',
    `user_name` VARCHAR(50) NOT NULL COMMENT '用户名',
    `nick_name` VARCHAR(50) DEFAULT NULL COMMENT '昵称',
    `password` VARCHAR(255) NOT NULL COMMENT '密码哈希',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态 1启用 0禁用',
    `is_super_admin` TINYINT NOT NULL DEFAULT 0 COMMENT '是否超级管理员 1是 0否',
    `login_time` DATETIME DEFAULT NULL COMMENT '最近登录时间',
    `login_address` VARCHAR(128) DEFAULT NULL COMMENT '最近登录地址',
    `pwd_update_date` DATETIME DEFAULT NULL COMMENT '密码最后更新时间',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '新增时间',
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `is_deleted` TINYINT NOT NULL DEFAULT 0 COMMENT '是否删除 0否 1是',
    `create_by_id` BIGINT DEFAULT NULL COMMENT '新增人ID',
    `create_by` VARCHAR(50) DEFAULT NULL COMMENT '新增人名称',
    `update_by_id` BIGINT DEFAULT NULL COMMENT '更新人ID',
    `update_by` VARCHAR(50) DEFAULT NULL COMMENT '更新人名称',
    `version` BIGINT DEFAULT 0 COMMENT '乐观锁版本',
    `remark` VARCHAR(500) DEFAULT NULL COMMENT '备注',
    UNIQUE KEY `uk_user_name` (`user_name`),
    KEY `idx_user_user_name` (`user_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户信息表';

CREATE TABLE IF NOT EXISTS `sys_login_log` (
    `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT 'ID',
    `tenant_id` BIGINT DEFAULT NULL COMMENT '租户ID',
    `user_id` BIGINT DEFAULT NULL COMMENT '用户ID',
    `token_id` BIGINT DEFAULT NULL COMMENT '登录令牌ID',
    `user_name` VARCHAR(50) DEFAULT '' COMMENT '用户名',
    `ipaddr` VARCHAR(128) DEFAULT '' COMMENT '登录IP',
    `login_location` VARCHAR(255) DEFAULT '' COMMENT '登录地点',
    `browser` VARCHAR(50) DEFAULT '' COMMENT '浏览器',
    `os` VARCHAR(50) DEFAULT '' COMMENT '操作系统',
    `status` CHAR(1) DEFAULT '0' COMMENT '登录状态 0成功 1失败',
    `msg` VARCHAR(255) DEFAULT '' COMMENT '提示消息',
    `token_jti` VARCHAR(64) DEFAULT NULL COMMENT 'JWT令牌JTI',
    `login_time` DATETIME DEFAULT NULL COMMENT '登录时间',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '新增时间',
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `is_deleted` TINYINT NOT NULL DEFAULT 0 COMMENT '是否删除 0否 1是',
    `create_by_id` BIGINT DEFAULT NULL COMMENT '新增人ID',
    `create_by` VARCHAR(50) DEFAULT NULL COMMENT '新增人名称',
    `update_by_id` BIGINT DEFAULT NULL COMMENT '更新人ID',
    `update_by` VARCHAR(50) DEFAULT NULL COMMENT '更新人名称',
    `version` BIGINT DEFAULT 0 COMMENT '乐观锁版本',
    `remark` VARCHAR(500) DEFAULT NULL COMMENT '备注',
    KEY `idx_login_log_status` (`status`),
    KEY `idx_login_log_time` (`login_time`),
    KEY `idx_login_log_user_id` (`user_id`),
    KEY `idx_login_log_token_id` (`token_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='登录审计日志表';

CREATE TABLE IF NOT EXISTS `sys_login_token` (
    `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT 'ID',
    `tenant_id` BIGINT DEFAULT NULL COMMENT '租户ID',
    `user_id` BIGINT NOT NULL COMMENT '用户ID',
    `user_name` VARCHAR(50) NOT NULL COMMENT '用户名',
    `token_jti` VARCHAR(64) NOT NULL COMMENT 'JWT令牌JTI',
    `token_digest` CHAR(64) NOT NULL COMMENT 'JWT令牌摘要',
    `login_ip` VARCHAR(128) DEFAULT '' COMMENT '登录IP',
    `user_agent` VARCHAR(500) DEFAULT '' COMMENT '客户端标识',
    `issued_at` DATETIME NOT NULL COMMENT '签发时间',
    `expires_at` DATETIME NOT NULL COMMENT '过期时间',
    `revoked_time` DATETIME DEFAULT NULL COMMENT '注销时间',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '新增时间',
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `is_deleted` TINYINT NOT NULL DEFAULT 0 COMMENT '是否删除 0否 1是',
    `create_by_id` BIGINT DEFAULT NULL COMMENT '新增人ID',
    `create_by` VARCHAR(50) DEFAULT NULL COMMENT '新增人名称',
    `update_by_id` BIGINT DEFAULT NULL COMMENT '更新人ID',
    `update_by` VARCHAR(50) DEFAULT NULL COMMENT '更新人名称',
    `version` BIGINT DEFAULT 0 COMMENT '乐观锁版本',
    `remark` VARCHAR(500) DEFAULT NULL COMMENT '备注',
    UNIQUE KEY `uk_login_token_jti` (`token_jti`),
    KEY `idx_login_token_user_id` (`user_id`),
    KEY `idx_login_token_expires_at` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='JWT登录令牌表';

CREATE TABLE IF NOT EXISTS `sys_supervisor_service` (
    `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT 'ID',
    `host_ip` VARCHAR(64) NOT NULL COMMENT '目标主机IP',
    `config_path` VARCHAR(500) NOT NULL COMMENT '相对 /etc/supervisord.d 的配置路径',
    `file_name` VARCHAR(255) NOT NULL COMMENT '配置文件 basename',
    `content_program_name` VARCHAR(255) NOT NULL COMMENT '配置内容中的 program_name',
    `manage_mode` VARCHAR(32) NOT NULL DEFAULT 'TEMPLATE_MANAGED' COMMENT '纳管模式',
    `baseline_content` MEDIUMTEXT DEFAULT NULL COMMENT '模板基线或导入原文快照',
    `metadata_complete` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '结构化字段是否完整',
    `parse_warnings` TEXT DEFAULT NULL COMMENT '解析告警JSON',
    `job_name` VARCHAR(128) DEFAULT NULL COMMENT '业务作业名称',
    `module_name` VARCHAR(128) DEFAULT NULL COMMENT '模块名称',
    `java_path` VARCHAR(500) DEFAULT NULL COMMENT 'Java可执行文件绝对路径',
    `active_profile` VARCHAR(64) DEFAULT NULL COMMENT 'Spring profile环境',
    `port` INT DEFAULT NULL COMMENT '服务监听端口',
    `jar_name` VARCHAR(255) DEFAULT NULL COMMENT 'Jar包文件名',
    `xms` VARCHAR(32) DEFAULT NULL COMMENT 'JVM Xms 参数',
    `xmx` VARCHAR(32) DEFAULT NULL COMMENT 'JVM Xmx 参数',
    `run_user` VARCHAR(64) DEFAULT NULL COMMENT 'Supervisor运行用户',
    `status` VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN' COMMENT '运行状态快照：RUNNING/STOPPED/FATAL/BACKOFF/STARTING/STOPPING/EXITED/UNKNOWN',
    `pid` VARCHAR(32) DEFAULT NULL COMMENT '进程PID',
    `uptime` VARCHAR(64) DEFAULT NULL COMMENT '运行时长',
    `status_sync_time` DATETIME DEFAULT NULL COMMENT '最近状态同步时间',
    `command` VARCHAR(2000) DEFAULT NULL COMMENT '最近同步到的 command 原文',
    `directory` VARCHAR(1000) DEFAULT NULL COMMENT '最近同步到的工作目录',
    `stdout_logfile` VARCHAR(1000) DEFAULT NULL COMMENT '最近同步到的 stdout_logfile',
    `has_backup` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '当前配置是否存在 .bak 备份',
    `config_content` MEDIUMTEXT DEFAULT NULL COMMENT '最近同步到的当前配置原文',
    `backup_config_content` MEDIUMTEXT DEFAULT NULL COMMENT '最近同步到的备份配置原文',
    `last_sync_at` DATETIME DEFAULT NULL COMMENT '最近执行详情同步时间',
    `sync_status` VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' COMMENT '详情同步状态：SUCCESS/FAILED/UNKNOWN',
    `sync_error` VARCHAR(1000) DEFAULT NULL COMMENT '最近一次详情同步错误摘要',
    `is_archived` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否已归档 0否 1是',
    `archived_at` DATETIME DEFAULT NULL COMMENT '归档时间',
    `restored_at` DATETIME DEFAULT NULL COMMENT '最近还原时间',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '新增时间',
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `create_by_id` BIGINT DEFAULT NULL COMMENT '新增人ID',
    `create_by` VARCHAR(50) DEFAULT NULL COMMENT '新增人名称',
    `update_by_id` BIGINT DEFAULT NULL COMMENT '更新人ID',
    `update_by` VARCHAR(50) DEFAULT NULL COMMENT '更新人名称',
    `remark` VARCHAR(500) DEFAULT NULL COMMENT '备注',
    UNIQUE KEY `uk_supervisor_host_config_path` (`host_ip`, `config_path`),
    KEY `idx_supervisor_host_program` (`host_ip`, `content_program_name`),
    KEY `idx_supervisor_host_manage_mode` (`host_ip`, `manage_mode`),
    KEY `idx_supervisor_host_archived` (`host_ip`, `is_archived`),
    KEY `idx_supervisor_host_status` (`host_ip`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Supervisor服务主数据表';

CREATE TABLE IF NOT EXISTS `sys_supervisor_import_staging` (
    `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT 'ID',
    `batch_id` VARCHAR(36) NOT NULL COMMENT '预检批次唯一标识(UUID)',
    `host_ip` VARCHAR(64) NOT NULL COMMENT '目标主机IP',
    `operator_id` BIGINT NOT NULL COMMENT '操作人ID',
    `operator_name` VARCHAR(50) NOT NULL COMMENT '操作人名称',
    `config_path` VARCHAR(500) NOT NULL COMMENT '相对 /etc/supervisord.d 的配置路径',
    `file_name` VARCHAR(255) NOT NULL COMMENT '配置文件 basename',
    `content_program_name` VARCHAR(255) DEFAULT NULL COMMENT '配置内容中的 program_name',
    `baseline_content` MEDIUMTEXT DEFAULT NULL COMMENT '配置原文快照',
    `metadata_complete` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '结构化字段是否完整',
    `parse_warnings` TEXT DEFAULT NULL COMMENT '解析告警JSON',
    `job_name` VARCHAR(128) DEFAULT NULL COMMENT '业务作业名称',
    `module_name` VARCHAR(128) DEFAULT NULL COMMENT '模块名称',
    `java_path` VARCHAR(500) DEFAULT NULL COMMENT 'Java可执行文件绝对路径',
    `active_profile` VARCHAR(64) DEFAULT NULL COMMENT 'Spring profile环境',
    `port` INT DEFAULT NULL COMMENT '服务监听端口',
    `jar_name` VARCHAR(255) DEFAULT NULL COMMENT 'Jar包文件名',
    `xms` VARCHAR(32) DEFAULT NULL COMMENT 'JVM Xms 参数',
    `xmx` VARCHAR(32) DEFAULT NULL COMMENT 'JVM Xmx 参数',
    `run_user` VARCHAR(64) DEFAULT NULL COMMENT 'Supervisor运行用户',
    `result` VARCHAR(16) NOT NULL COMMENT '预检结果：PLANNED/SKIPPED',
    `message` VARCHAR(500) DEFAULT NULL COMMENT '预检消息或跳过原因',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '新增时间',
    KEY `idx_staging_batch_id` (`batch_id`),
    KEY `idx_staging_host_ip` (`host_ip`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Supervisor导入预检暂存表';

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
);
