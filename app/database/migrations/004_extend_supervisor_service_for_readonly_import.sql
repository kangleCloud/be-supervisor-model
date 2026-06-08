ALTER TABLE `sys_supervisor_service`
    MODIFY `job_name` VARCHAR(128) DEFAULT NULL COMMENT '业务作业名称',
    MODIFY `module_name` VARCHAR(128) DEFAULT NULL COMMENT '模块名称',
    MODIFY `java_path` VARCHAR(500) DEFAULT NULL COMMENT 'Java可执行文件绝对路径',
    MODIFY `active_profile` VARCHAR(64) DEFAULT NULL COMMENT 'Spring profile环境',
    MODIFY `port` INT DEFAULT NULL COMMENT '服务监听端口',
    MODIFY `jar_name` VARCHAR(255) DEFAULT NULL COMMENT 'Jar包文件名',
    MODIFY `xms` VARCHAR(32) DEFAULT NULL COMMENT 'JVM Xms 参数',
    MODIFY `xmx` VARCHAR(32) DEFAULT NULL COMMENT 'JVM Xmx 参数',
    MODIFY `run_user` VARCHAR(64) DEFAULT NULL COMMENT 'Supervisor运行用户';

ALTER TABLE `sys_supervisor_service`
    ADD COLUMN `config_path` VARCHAR(500) DEFAULT NULL COMMENT '相对 /etc/supervisord.d 的配置路径' AFTER `config_name`,
    ADD COLUMN `file_name` VARCHAR(255) DEFAULT NULL COMMENT '配置文件 basename' AFTER `config_path`,
    ADD COLUMN `content_program_name` VARCHAR(255) DEFAULT NULL COMMENT '配置内容中的 program_name' AFTER `file_name`,
    ADD COLUMN `manage_mode` VARCHAR(32) NOT NULL DEFAULT 'TEMPLATE_MANAGED' COMMENT '纳管模式' AFTER `content_program_name`,
    ADD COLUMN `baseline_content` MEDIUMTEXT DEFAULT NULL COMMENT '模板基线或导入原文快照' AFTER `manage_mode`,
    ADD COLUMN `metadata_complete` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '结构化字段是否完整' AFTER `baseline_content`,
    ADD COLUMN `parse_warnings` TEXT DEFAULT NULL COMMENT '解析告警JSON' AFTER `metadata_complete`;

UPDATE `sys_supervisor_service`
SET `config_path` = IFNULL(`config_path`, `config_name`),
    `file_name` = IFNULL(`file_name`, `config_name`),
    `content_program_name` = IFNULL(`content_program_name`, `program_name`),
    `program_name` = IFNULL(`program_name`, `content_program_name`),
    `config_name` = IFNULL(`config_name`, `file_name`),
    `manage_mode` = IFNULL(`manage_mode`, 'TEMPLATE_MANAGED'),
    `baseline_content` = IFNULL(`baseline_content`, ''),
    `metadata_complete` = IFNULL(`metadata_complete`, 1),
    `parse_warnings` = IFNULL(`parse_warnings`, '[]');

ALTER TABLE `sys_supervisor_service`
    DROP INDEX `uk_supervisor_host_program`,
    DROP INDEX `uk_supervisor_host_config`,
    DROP INDEX `uk_supervisor_host_port`;

ALTER TABLE `sys_supervisor_service`
    ADD UNIQUE KEY `uk_supervisor_host_config_path` (`host_ip`, `config_path`),
    ADD KEY `idx_supervisor_host_program` (`host_ip`, `program_name`),
    ADD KEY `idx_supervisor_host_manage_mode` (`host_ip`, `manage_mode`);
