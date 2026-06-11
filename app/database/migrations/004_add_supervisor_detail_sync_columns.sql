ALTER TABLE `sys_supervisor_service`
    ADD COLUMN `command` VARCHAR(2000) DEFAULT NULL COMMENT '最近同步到的 command 原文' AFTER `status_sync_time`,
    ADD COLUMN `directory` VARCHAR(1000) DEFAULT NULL COMMENT '最近同步到的工作目录' AFTER `command`,
    ADD COLUMN `stdout_logfile` VARCHAR(1000) DEFAULT NULL COMMENT '最近同步到的 stdout_logfile' AFTER `directory`,
    ADD COLUMN `has_backup` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '当前配置是否存在 .bak 备份' AFTER `stdout_logfile`,
    ADD COLUMN `config_content` MEDIUMTEXT DEFAULT NULL COMMENT '最近同步到的当前配置原文' AFTER `has_backup`,
    ADD COLUMN `backup_config_content` MEDIUMTEXT DEFAULT NULL COMMENT '最近同步到的备份配置原文' AFTER `config_content`,
    ADD COLUMN `last_sync_at` DATETIME DEFAULT NULL COMMENT '最近执行详情同步时间' AFTER `backup_config_content`,
    ADD COLUMN `sync_status` VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' COMMENT '详情同步状态：SUCCESS/FAILED/UNKNOWN' AFTER `last_sync_at`,
    ADD COLUMN `sync_error` VARCHAR(1000) DEFAULT NULL COMMENT '最近一次详情同步错误摘要' AFTER `sync_status`;
