-- 仅用于兼容旧版 sys_supervisor_service 缺少归档字段的场景。
-- 新库初始化时 001 已包含这些列，服务端会在执行前先判断是否需要真正补建。
ALTER TABLE `sys_supervisor_service`
    ADD COLUMN `is_archived` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否已归档 0否 1是' AFTER `status_sync_time`,
    ADD COLUMN `archived_at` DATETIME DEFAULT NULL COMMENT '归档时间' AFTER `is_archived`,
    ADD COLUMN `restored_at` DATETIME DEFAULT NULL COMMENT '最近还原时间' AFTER `archived_at`,
    ADD KEY `idx_supervisor_host_archived` (`host_ip`, `is_archived`);
