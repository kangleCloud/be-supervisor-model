-- 仅用于兼容旧版 sys_supervisor_service 缺少运行时字段的场景。
-- 新库初始化时 001 已包含这些列，服务端会在执行前先判断是否需要真正补建。
ALTER TABLE `sys_supervisor_service`
    ADD COLUMN `status` VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN' COMMENT '运行状态快照：RUNNING/STOPPED/FATAL/BACKOFF/STARTING/STOPPING/EXITED/UNKNOWN' AFTER `run_user`,
    ADD COLUMN `pid` VARCHAR(32) DEFAULT NULL COMMENT '进程PID' AFTER `status`,
    ADD COLUMN `uptime` VARCHAR(64) DEFAULT NULL COMMENT '运行时长' AFTER `pid`,
    ADD COLUMN `status_sync_time` DATETIME DEFAULT NULL COMMENT '最近状态同步时间' AFTER `uptime`,
    ADD KEY `idx_supervisor_host_status` (`host_ip`, `status`);
