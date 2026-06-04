-- 初始化默认超级管理员。
-- 当前为内网管理工具，默认内置一条 admin 账号，便于空库首次启动直接登录。
INSERT INTO sys_user(
    id, tenant_id, user_name, nick_name, password, status, is_super_admin,
    pwd_update_date, create_by_id, create_by, update_by_id, update_by, version, remark
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
    SELECT COALESCE(MAX(id), 0) + 1 AS next_id
    FROM sys_user
) AS next_user
WHERE NOT EXISTS (
    SELECT 1
    FROM sys_user
    WHERE user_name = 'admin'
      AND is_deleted = 0
);
