from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

SUPERVISOR_SERVICE_TABLE = "sys_supervisor_service"
LEGACY_COLUMNS = frozenset({"program_name", "config_name"})


async def _table_exists(db: BaseDBAsyncClient) -> bool:
    rows = await db.execute_query_dict(
        """
        SELECT TABLE_NAME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        [SUPERVISOR_SERVICE_TABLE],
    )
    return bool(rows)


async def _load_columns(db: BaseDBAsyncClient) -> set[str]:
    rows = await db.execute_query_dict(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        [SUPERVISOR_SERVICE_TABLE],
    )
    return {str(row["COLUMN_NAME"]) for row in rows}


async def _load_indexes(db: BaseDBAsyncClient) -> list[tuple[str, tuple[str, ...], bool]]:
    rows = await db.execute_query_dict(
        """
        SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE, SEQ_IN_INDEX
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        ORDER BY INDEX_NAME ASC, SEQ_IN_INDEX ASC
        """,
        [SUPERVISOR_SERVICE_TABLE],
    )
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        index_name = str(row["INDEX_NAME"])
        bucket = grouped.setdefault(
            index_name,
            {
                "unique": int(row["NON_UNIQUE"]) == 0,
                "columns": [],
            },
        )
        columns = bucket["columns"]
        assert isinstance(columns, list)
        columns.append(str(row["COLUMN_NAME"]))
    return [
        (name, tuple(item["columns"]), bool(item["unique"]))
        for name, item in grouped.items()
    ]


def _coalesce_expr(parts: list[str]) -> str | None:
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return f"COALESCE({', '.join(parts)})"


def _append_backfill_statement(statements: list[str], *, target_column: str, expr: str) -> None:
    statements.append(
        f"""
        UPDATE `{SUPERVISOR_SERVICE_TABLE}`
        SET `{target_column}` = {expr}
        WHERE (`{target_column}` IS NULL OR `{target_column}` = '')
          AND {expr} IS NOT NULL
        """.strip()
    )


def _has_index_shape(indexes: list[tuple[str, tuple[str, ...], bool]], *, columns: tuple[str, ...], unique: bool) -> bool:
    return any(index_columns == columns and index_unique is unique for _, index_columns, index_unique in indexes)


def _legacy_index_names(indexes: list[tuple[str, tuple[str, ...], bool]]) -> list[str]:
    names: list[str] = []
    for index_name, columns, unique in indexes:
        if any(column in LEGACY_COLUMNS for column in columns):
            names.append(index_name)
            continue
        if unique and columns == ("host_ip", "port"):
            names.append(index_name)
    return sorted(set(names))


async def upgrade(db: BaseDBAsyncClient) -> str:
    if db.schema_generator.DIALECT != "mysql":
        return ""
    if not await _table_exists(db):
        return ""

    columns = await _load_columns(db)
    indexes = await _load_indexes(db)
    statements: list[str] = []

    has_config_path = "config_path" in columns
    has_file_name = "file_name" in columns
    has_content_program_name = "content_program_name" in columns

    # 旧库若只保留 program_name/config_name，需要先补齐当前主表最基本的真实字段。
    if not has_config_path:
        statements.append(
            f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` "
            "ADD COLUMN `config_path` VARCHAR(500) DEFAULT NULL COMMENT '相对 /etc/supervisord.d 的配置路径'"
        )
        has_config_path = True
    if not has_file_name:
        statements.append(
            f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` "
            "ADD COLUMN `file_name` VARCHAR(255) DEFAULT NULL COMMENT '配置文件 basename'"
        )
        has_file_name = True
    if not has_content_program_name:
        statements.append(
            f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` "
            "ADD COLUMN `content_program_name` VARCHAR(255) DEFAULT NULL COMMENT '配置内容中的 program_name'"
        )
        has_content_program_name = True

    if has_content_program_name and "program_name" in columns:
        expr = _coalesce_expr(["NULLIF(`program_name`, '')"])
        if expr is not None:
            _append_backfill_statement(statements, target_column="content_program_name", expr=expr)

    if has_file_name:
        file_name_sources: list[str] = []
        if "config_name" in columns:
            file_name_sources.append("NULLIF(`config_name`, '')")
        if has_config_path:
            file_name_sources.append("NULLIF(SUBSTRING_INDEX(`config_path`, '/', -1), '')")
        expr = _coalesce_expr(file_name_sources)
        if expr is not None:
            _append_backfill_statement(statements, target_column="file_name", expr=expr)

    if has_config_path:
        config_path_sources: list[str] = []
        if "config_name" in columns:
            config_path_sources.append("NULLIF(`config_name`, '')")
        if has_file_name:
            config_path_sources.append("NULLIF(`file_name`, '')")
        expr = _coalesce_expr(config_path_sources)
        if expr is not None:
            _append_backfill_statement(statements, target_column="config_path", expr=expr)

    # 先删旧索引，再删旧列，避免 MySQL 因索引依赖旧列而拒绝 DROP COLUMN。
    for legacy_index_name in _legacy_index_names(indexes):
        statements.append(f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` DROP INDEX `{legacy_index_name}`")

    if "program_name" in columns:
        statements.append(f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` DROP COLUMN `program_name`")
    if "config_name" in columns:
        statements.append(f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` DROP COLUMN `config_name`")

    if not _has_index_shape(indexes, columns=("host_ip", "config_path"), unique=True):
        statements.append(
            f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` "
            "ADD UNIQUE KEY `uk_supervisor_host_config_path` (`host_ip`, `config_path`)"
        )
    if not _has_index_shape(indexes, columns=("host_ip", "content_program_name"), unique=False):
        statements.append(
            f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` "
            "ADD KEY `idx_supervisor_host_program` (`host_ip`, `content_program_name`)"
        )
    if not _has_index_shape(indexes, columns=("host_ip", "manage_mode"), unique=False):
        statements.append(
            f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` "
            "ADD KEY `idx_supervisor_host_manage_mode` (`host_ip`, `manage_mode`)"
        )
    if not _has_index_shape(indexes, columns=("host_ip", "is_archived"), unique=False):
        statements.append(
            f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` "
            "ADD KEY `idx_supervisor_host_archived` (`host_ip`, `is_archived`)"
        )
    if not _has_index_shape(indexes, columns=("host_ip", "status"), unique=False):
        statements.append(
            f"ALTER TABLE `{SUPERVISOR_SERVICE_TABLE}` "
            "ADD KEY `idx_supervisor_host_status` (`host_ip`, `status`)"
        )

    if not statements:
        return ""
    return ";\n".join(statements) + ";"


async def downgrade(db: BaseDBAsyncClient) -> str:
    return ""
