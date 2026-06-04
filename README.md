# be-supervisor-model

`be-supervisor-model` 是一个基于 FastAPI 的运维管理服务，用于集中管理目标主机上的 Supervisor 配置文件和进程状态。

它面向运维后台或后续前端管理系统，提供主机查询、服务配置新增/修改/删除、备份还原、端口冲突检测，以及 `supervisorctl` 常用命令的 REST API。

当前版本已经内置轻量登录鉴权：

- 认证接口统一走 `/admin/api/auth/*`
- Supervisor 管理接口统一走 `/admin/api/supervisor/*`
- 使用 `Authorization: Bearer <jwt>` 访问受保护接口
- 登录令牌与登录审计落到 MySQL 8

## 技术栈

- Python 3.12
- FastAPI
- Pydantic
- Jinja2
- PyYAML
- PyMySQL
- PyJWT
- bcrypt
- pytest

## 执行模型

服务支持两类执行方式，并由主机配置决定：

- `local`：当管理服务与被管理应用在同一台服务器时，直接在本机操作配置文件并执行 `supervisorctl`
- `ansible`：当需要管理远程主机时，通过 `ansible` ad-hoc 命令执行受控操作

配置文件默认目录：

```text
/etc/supervisord.d
```

仅允许操作：

- `*.ini`
- `*.ini.bak`
- `*.ini.bak.*`

## Supervisor 模板

模板文件位于 [app/templates/supervisor_program.ini.j2](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/app/templates/supervisor_program.ini.j2)。

默认渲染结果：

```ini
[program:demo_member]
command=/usr/local/jdk17/bin/java -jar -Xms128m -Xmx128m -Dspring.profiles.active=prod -Dserver.port=9001 /data/content/demo/member/member.jar
directory=/data/content/demo/member
autostart=true
startsecs=10
autorestart=true
startretries=3
user=root
priority=999
redirect_stderr=true
stdout_logfile_maxbytes=1GB
stdout_logfile_backups=1
stopasgroup=false
killasgroup=false
stdout_logfile=/data/logs/demo/demo-member.log
```

默认规则：

- `programName = {jobName}_{moduleName}`
- `configName = {programName}.ini`
- `jarName` 为空时自动使用 `{moduleName}.jar`
- 以下 Supervisor 参数是系统固定策略，前端不需要也不能传入：
  - `autostart=true`
  - `startsecs=10`
  - `autorestart=true`
  - `startretries=3`
  - `priority=999`
  - `redirect_stderr=true`
  - `stdout_logfile_maxbytes=1GB`
  - `stdout_logfile_backups=1`
  - `stopasgroup=false`
  - `killasgroup=false`
- `autoStart` 仅用于控制本次新增或修改接口执行完成后是否立即启动服务，不会改变模板中的 `autostart=true`

## 配置

复制示例配置：

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

关键配置项：

- `APP_HOST` / `APP_PORT`
- `DATABASE_HOST` / `DATABASE_PORT` / `DATABASE_NAME`
- `DATABASE_USER` / `DATABASE_PASSWORD`
- `JWT_SECRET`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `SUPERVISOR_CONF_DIR`
- `COMMAND_TIMEOUT_SECONDS`
- `EXECUTOR_TYPE`
- `ANSIBLE_INVENTORY_PATH`

主机列表示例见 [config.example.yaml](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/config.example.yaml:1)。

数据库启动规则：

- 服务启动时会自动创建目标数据库（若不存在）
- 会自动执行 `app/database/migrations/*.sql` 中的表结构迁移
- 账号不提供 HTTP 创建接口，需要运维手工插入 `sys_user`

密码哈希可以使用脚本生成：

```bash
python scripts/hash_password.py
```

## 业务文档

仓库业务文档统一放在 `docs/` 目录，采用 `docs/NN.主题.md` 的专题文档编号方式。

- 当前基线文档见 [docs/01.业务流程说明.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/01.业务流程说明.md)
- 后续每次系统业务修改，都会继续新增 `docs/NN.主题.md` 专题文档

## 启动方式

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 18880
```

也可以使用脚本：

```bash
./scripts/run.sh
```

## API 列表

- `POST /admin/api/auth/login`
- `GET /admin/api/auth/profile`
- `POST /admin/api/auth/logout`
- `GET /admin/api/supervisor/hosts`
- `GET /admin/api/supervisor/services?host=127.0.0.1`
- `GET /admin/api/supervisor/services/{programName}?host=127.0.0.1`
- `POST /admin/api/supervisor/services`
- `PUT /admin/api/supervisor/services/{programName}`
- `DELETE /admin/api/supervisor/services/{programName}?host=127.0.0.1&deleteBackup=false`
- `POST /admin/api/supervisor/services/{programName}/start`
- `POST /admin/api/supervisor/services/{programName}/stop`
- `POST /admin/api/supervisor/services/{programName}/restart`
- `POST /admin/api/supervisor/services/{programName}/backup`
- `POST /admin/api/supervisor/services/{programName}/restore`
- `GET /admin/api/supervisor/ports/check?host=127.0.0.1&port=9001`
- `POST /admin/api/supervisor/reread`
- `POST /admin/api/supervisor/update`
- `GET /admin/api/supervisor/status?host=127.0.0.1`

统一响应格式：

```json
{
  "code": 200,
  "msg": "success",
  "data": {}
}
```

## 示例

登录获取 Token：

```bash
curl -X POST http://127.0.0.1:18880/admin/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{
    "username": "ops",
    "password": "secret"
  }'
```

新增服务：

```bash
curl -X POST http://127.0.0.1:18880/admin/api/supervisor/services \
  -H 'Authorization: Bearer <access-token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "host": "127.0.0.1",
    "jobName": "demo-project",
    "moduleName": "member",
    "javaPath": "/usr/local/jdk17/bin/java",
    "active": "prod",
    "port": 9001,
    "jarName": "member.jar",
    "configName": "",
    "xms": "128m",
    "xmx": "128m",
    "user": "root",
    "autoStart": false
  }'
```

修改服务并自动重命名：

```bash
curl -X PUT http://127.0.0.1:18880/admin/api/supervisor/services/demo-project_member \
  -H 'Authorization: Bearer <access-token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "host": "127.0.0.1",
    "jobName": "demo-project",
    "moduleName": "member-v2",
    "javaPath": "/usr/local/jdk17/bin/java",
    "active": "prod",
    "port": 9002,
    "jarName": "member-v2.jar",
    "configName": "",
    "xms": "128m",
    "xmx": "128m",
    "user": "root",
    "autoStart": false
  }'
```

删除服务：

```bash
curl -X DELETE 'http://127.0.0.1:18880/admin/api/supervisor/services/demo-project_member?host=127.0.0.1&deleteBackup=false' \
  -H 'Authorization: Bearer <access-token>'
```

备份配置：

```bash
curl -X POST http://127.0.0.1:18880/admin/api/supervisor/services/demo-project_member/backup \
  -H 'Authorization: Bearer <access-token>' \
  -H 'Content-Type: application/json' \
  -d '{"host":"127.0.0.1"}'
```

还原配置：

```bash
curl -X POST http://127.0.0.1:18880/admin/api/supervisor/services/demo-project_member/restore \
  -H 'Authorization: Bearer <access-token>' \
  -H 'Content-Type: application/json' \
  -d '{"host":"127.0.0.1"}'
```

检测端口冲突：

```bash
curl 'http://127.0.0.1:18880/admin/api/supervisor/ports/check?host=127.0.0.1&port=9001' \
  -H 'Authorization: Bearer <access-token>'
```

## 生产安全建议

- 目标主机必须通过配置白名单声明，不允许调用方传入任意目标
- `JWT_SECRET` 必须使用高强度随机值，禁止直接沿用示例配置
- MySQL 账号只授予当前业务库的最小权限
- 严格限制 `sudoers` 和提权代理只允许执行受控命令
- `ansible` 节点需要最小权限访问 Inventory 与 SSH 凭据
- 不要把生产数据库密码、JWT 密钥、Inventory、SSH 密钥提交到仓库
- 配置目录必须固定在 `/etc/supervisord.d`

## 验收命令

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --host 0.0.0.0 --port 18880
```

接口验证：

```bash
curl -X POST http://127.0.0.1:18880/admin/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"ops","password":"secret"}'
```

```bash
curl 'http://127.0.0.1:18880/admin/api/supervisor/ports/check?host=127.0.0.1&port=9001' \
  -H 'Authorization: Bearer <access-token>'
```
