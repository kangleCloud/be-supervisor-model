-- 旧库升级脚本：把 sys_supervisor_service 从旧字段/旧索引形态升级到当前最终结构。
-- 执行前提：目标库已经存在 sys_supervisor_service；若是新库，请先执行 001_init_schema.sql。
-- 本脚本设计为可重复执行；每一步都会先检查当前 schema，再决定是否变更。

DELIMITER $$

DROP PROCEDURE IF EXISTS `fix_supervisor_service_legacy_schema`$$
CREATE PROCEDURE `fix_supervisor_service_legacy_schema`()
BEGIN
    DECLARE v_table_exists INT DEFAULT 0;
    DECLARE v_config_path_exists INT DEFAULT 0;
    DECLARE v_file_name_exists INT DEFAULT 0;
    DECLARE v_content_program_name_exists INT DEFAULT 0;
    DECLARE v_program_name_exists INT DEFAULT 0;
    DECLARE v_config_name_exists INT DEFAULT 0;
    DECLARE v_has_host_config_path_unique INT DEFAULT 0;
    DECLARE v_has_host_program_index INT DEFAULT 0;
    DECLARE v_has_host_manage_mode_index INT DEFAULT 0;
    DECLARE v_has_host_archived_index INT DEFAULT 0;
    DECLARE v_has_host_status_index INT DEFAULT 0;
    DECLARE v_legacy_index_name VARCHAR(128);
    DECLARE v_done INT DEFAULT 0;

    DECLARE legacy_index_cursor CURSOR FOR
        SELECT DISTINCT legacy_indexes.index_name
        FROM (
            SELECT s.INDEX_NAME AS index_name
            FROM information_schema.STATISTICS s
            WHERE s.TABLE_SCHEMA = DATABASE()
              AND s.TABLE_NAME = 'sys_supervisor_service'
            GROUP BY s.INDEX_NAME
            HAVING SUM(CASE WHEN s.COLUMN_NAME IN ('program_name', 'config_name') THEN 1 ELSE 0 END) > 0

            UNION

            SELECT s2.INDEX_NAME AS index_name
            FROM information_schema.STATISTICS s2
            WHERE s2.TABLE_SCHEMA = DATABASE()
              AND s2.TABLE_NAME = 'sys_supervisor_service'
            GROUP BY s2.INDEX_NAME
            HAVING MAX(CASE WHEN s2.NON_UNIQUE = 0 THEN 1 ELSE 0 END) = 1
               AND GROUP_CONCAT(s2.COLUMN_NAME ORDER BY s2.SEQ_IN_INDEX SEPARATOR ',') = 'host_ip,port'
        ) AS legacy_indexes
        WHERE legacy_indexes.index_name <> 'PRIMARY';

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;

    SELECT COUNT(*)
    INTO v_table_exists
    FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sys_supervisor_service';

    IF v_table_exists = 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '缺少表 sys_supervisor_service，请先执行 app/database/migrations/001_init_schema.sql';
    END IF;

    SELECT COUNT(*) INTO v_config_path_exists
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sys_supervisor_service'
      AND COLUMN_NAME = 'config_path';

    SELECT COUNT(*) INTO v_file_name_exists
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sys_supervisor_service'
      AND COLUMN_NAME = 'file_name';

    SELECT COUNT(*) INTO v_content_program_name_exists
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sys_supervisor_service'
      AND COLUMN_NAME = 'content_program_name';

    SELECT COUNT(*) INTO v_program_name_exists
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sys_supervisor_service'
      AND COLUMN_NAME = 'program_name';

    SELECT COUNT(*) INTO v_config_name_exists
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sys_supervisor_service'
      AND COLUMN_NAME = 'config_name';

    IF v_config_path_exists = 0 THEN
        ALTER TABLE `sys_supervisor_service`
        ADD COLUMN `config_path` VARCHAR(500) DEFAULT NULL COMMENT '相对 /etc/supervisord.d 的配置路径';
        SET v_config_path_exists = 1;
    END IF;

    IF v_file_name_exists = 0 THEN
        ALTER TABLE `sys_supervisor_service`
        ADD COLUMN `file_name` VARCHAR(255) DEFAULT NULL COMMENT '配置文件 basename';
        SET v_file_name_exists = 1;
    END IF;

    IF v_content_program_name_exists = 0 THEN
        ALTER TABLE `sys_supervisor_service`
        ADD COLUMN `content_program_name` VARCHAR(255) DEFAULT NULL COMMENT '配置内容中的 program_name';
        SET v_content_program_name_exists = 1;
    END IF;

    IF v_content_program_name_exists = 1 AND v_program_name_exists = 1 THEN
        UPDATE `sys_supervisor_service`
        SET `content_program_name` = NULLIF(`program_name`, '')
        WHERE (`content_program_name` IS NULL OR `content_program_name` = '')
          AND NULLIF(`program_name`, '') IS NOT NULL;
    END IF;

    IF v_file_name_exists = 1 AND v_config_name_exists = 1 AND v_config_path_exists = 1 THEN
        UPDATE `sys_supervisor_service`
        SET `file_name` = COALESCE(
            NULLIF(`config_name`, ''),
            NULLIF(SUBSTRING_INDEX(`config_path`, '/', -1), '')
        )
        WHERE (`file_name` IS NULL OR `file_name` = '');
    ELSEIF v_file_name_exists = 1 AND v_config_name_exists = 1 THEN
        UPDATE `sys_supervisor_service`
        SET `file_name` = NULLIF(`config_name`, '')
        WHERE (`file_name` IS NULL OR `file_name` = '')
          AND NULLIF(`config_name`, '') IS NOT NULL;
    ELSEIF v_file_name_exists = 1 AND v_config_path_exists = 1 THEN
        UPDATE `sys_supervisor_service`
        SET `file_name` = NULLIF(SUBSTRING_INDEX(`config_path`, '/', -1), '')
        WHERE (`file_name` IS NULL OR `file_name` = '')
          AND NULLIF(SUBSTRING_INDEX(`config_path`, '/', -1), '') IS NOT NULL;
    END IF;

    IF v_config_path_exists = 1 AND v_config_name_exists = 1 AND v_file_name_exists = 1 THEN
        UPDATE `sys_supervisor_service`
        SET `config_path` = COALESCE(
            NULLIF(`config_name`, ''),
            NULLIF(`file_name`, '')
        )
        WHERE (`config_path` IS NULL OR `config_path` = '');
    ELSEIF v_config_path_exists = 1 AND v_config_name_exists = 1 THEN
        UPDATE `sys_supervisor_service`
        SET `config_path` = NULLIF(`config_name`, '')
        WHERE (`config_path` IS NULL OR `config_path` = '')
          AND NULLIF(`config_name`, '') IS NOT NULL;
    ELSEIF v_config_path_exists = 1 AND v_file_name_exists = 1 THEN
        UPDATE `sys_supervisor_service`
        SET `config_path` = NULLIF(`file_name`, '')
        WHERE (`config_path` IS NULL OR `config_path` = '')
          AND NULLIF(`file_name`, '') IS NOT NULL;
    END IF;

    OPEN legacy_index_cursor;
    drop_legacy_index_loop: LOOP
        FETCH legacy_index_cursor INTO v_legacy_index_name;
        IF v_done = 1 THEN
            LEAVE drop_legacy_index_loop;
        END IF;

        SET @drop_index_sql = CONCAT(
            'ALTER TABLE `sys_supervisor_service` DROP INDEX `',
            REPLACE(v_legacy_index_name, '`', '``'),
            '`'
        );
        PREPARE stmt FROM @drop_index_sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END LOOP;
    CLOSE legacy_index_cursor;

    IF v_program_name_exists = 1 THEN
        ALTER TABLE `sys_supervisor_service` DROP COLUMN `program_name`;
    END IF;

    IF v_config_name_exists = 1 THEN
        ALTER TABLE `sys_supervisor_service` DROP COLUMN `config_name`;
    END IF;

    SELECT COUNT(*)
    INTO v_has_host_config_path_unique
    FROM (
        SELECT s.INDEX_NAME
        FROM information_schema.STATISTICS s
        WHERE s.TABLE_SCHEMA = DATABASE()
          AND s.TABLE_NAME = 'sys_supervisor_service'
        GROUP BY s.INDEX_NAME
        HAVING MAX(CASE WHEN s.NON_UNIQUE = 0 THEN 1 ELSE 0 END) = 1
           AND GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX SEPARATOR ',') = 'host_ip,config_path'
    ) AS exact_indexes;

    IF v_has_host_config_path_unique = 0 THEN
        ALTER TABLE `sys_supervisor_service`
        ADD UNIQUE KEY `uk_supervisor_host_config_path` (`host_ip`, `config_path`);
    END IF;

    SELECT COUNT(*)
    INTO v_has_host_program_index
    FROM (
        SELECT s.INDEX_NAME
        FROM information_schema.STATISTICS s
        WHERE s.TABLE_SCHEMA = DATABASE()
          AND s.TABLE_NAME = 'sys_supervisor_service'
        GROUP BY s.INDEX_NAME
        HAVING MAX(CASE WHEN s.NON_UNIQUE = 1 THEN 1 ELSE 0 END) = 1
           AND GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX SEPARATOR ',') = 'host_ip,content_program_name'
    ) AS exact_indexes;

    IF v_has_host_program_index = 0 THEN
        ALTER TABLE `sys_supervisor_service`
        ADD KEY `idx_supervisor_host_program` (`host_ip`, `content_program_name`);
    END IF;

    SELECT COUNT(*)
    INTO v_has_host_manage_mode_index
    FROM (
        SELECT s.INDEX_NAME
        FROM information_schema.STATISTICS s
        WHERE s.TABLE_SCHEMA = DATABASE()
          AND s.TABLE_NAME = 'sys_supervisor_service'
        GROUP BY s.INDEX_NAME
        HAVING MAX(CASE WHEN s.NON_UNIQUE = 1 THEN 1 ELSE 0 END) = 1
           AND GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX SEPARATOR ',') = 'host_ip,manage_mode'
    ) AS exact_indexes;

    IF v_has_host_manage_mode_index = 0 THEN
        ALTER TABLE `sys_supervisor_service`
        ADD KEY `idx_supervisor_host_manage_mode` (`host_ip`, `manage_mode`);
    END IF;

    SELECT COUNT(*)
    INTO v_has_host_archived_index
    FROM (
        SELECT s.INDEX_NAME
        FROM information_schema.STATISTICS s
        WHERE s.TABLE_SCHEMA = DATABASE()
          AND s.TABLE_NAME = 'sys_supervisor_service'
        GROUP BY s.INDEX_NAME
        HAVING MAX(CASE WHEN s.NON_UNIQUE = 1 THEN 1 ELSE 0 END) = 1
           AND GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX SEPARATOR ',') = 'host_ip,is_archived'
    ) AS exact_indexes;

    IF v_has_host_archived_index = 0 THEN
        ALTER TABLE `sys_supervisor_service`
        ADD KEY `idx_supervisor_host_archived` (`host_ip`, `is_archived`);
    END IF;

    SELECT COUNT(*)
    INTO v_has_host_status_index
    FROM (
        SELECT s.INDEX_NAME
        FROM information_schema.STATISTICS s
        WHERE s.TABLE_SCHEMA = DATABASE()
          AND s.TABLE_NAME = 'sys_supervisor_service'
        GROUP BY s.INDEX_NAME
        HAVING MAX(CASE WHEN s.NON_UNIQUE = 1 THEN 1 ELSE 0 END) = 1
           AND GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX SEPARATOR ',') = 'host_ip,status'
    ) AS exact_indexes;

    IF v_has_host_status_index = 0 THEN
        ALTER TABLE `sys_supervisor_service`
        ADD KEY `idx_supervisor_host_status` (`host_ip`, `status`);
    END IF;
END$$

CALL `fix_supervisor_service_legacy_schema`()$$
DROP PROCEDURE IF EXISTS `fix_supervisor_service_legacy_schema`$$

DELIMITER ;
