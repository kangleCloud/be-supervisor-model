# be-supervisor-model

`be-supervisor-model` 是一个基于 FastAPI 的运维管理服务，用于统一管理目标主机上的 Supervisor 配置。

当前版本包含两类能力：

- 登录鉴权：登录、查询当前用户、退出登录
- Supervisor 管理：查询主机、查询纳管服务、查询服务详情、新增服务

Supervisor 配置主数据落在 MySQL 8 的 `sys_supervisor_service` 表中；远端 `/etc/supervisord.d/*.ini` 是实际生效结果；运行状态实时来自 `supervisorctl status`。

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

## 配置

通用环境变量示例见 [.env.example](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/.env.example)，当前项目实际使用：

- `.env.dev`：开发环境变量
- `.env.prod`：生产环境变量

复制 YAML 示例配置：

```bash
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
- `APP_ENV` / `APP_ENV_FILE`

环境变量加载优先级：

- 进程显式环境变量
- `.env.dev` / `.env.prod`
- `config.yaml`
- 代码默认值

环境文件选择规则：

- `APP_ENV_FILE` 优先级最高，必须传绝对路径
- 未设置 `APP_ENV_FILE` 时，`APP_ENV=dev` 加载 `.env.dev`
- 未设置 `APP_ENV_FILE` 时，`APP_ENV=prod` 加载 `.env.prod`
- 未设置 `APP_ENV` 时，保持旧行为，只读取进程环境变量和 `config.yaml`

主机列表示例见 [config.example.yaml](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/config.example.yaml:1)。

数据库启动规则：

- 服务启动时会自动创建目标数据库（若不存在）
- 会自动执行 `app/database/migrations/*.sql` 中的表结构迁移
- 会自动初始化一条超级管理员账号：`admin / Admin@123456`
- 其他账号不提供 HTTP 创建接口，需要运维手工插入 `sys_user`

密码哈希可以使用脚本生成：

```bash
python3 scripts/hash_password.py
```

## 导入现网配置

现网已有 Supervisor 配置时，需要先执行一次导入脚本，再使用“库为主”的服务列表和详情接口：

```bash
python3 scripts/import_supervisor_services.py
```

导入规则：

- 只导入 `*.ini`
- 不导入 `.bak` 和归档备份
- 只有能完整反解 `jobName/moduleName/javaPath/active/port/jarName/xms/xmx/user` 的配置才会入库

## 业务文档

- 基线业务说明见 [docs/01.业务流程说明.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/01.业务流程说明.md)
- 本次主数据化改造见 [docs/02.Supervisor管理表设计与同步流程.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/02.Supervisor管理表设计与同步流程.md)
- 本次跨域预检修复见 [docs/03.跨域预检与跨源访问说明.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/03.跨域预检与跨源访问说明.md)

## 启动方式

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./scripts/run.sh dev
```

生产环境启动：

```bash
./scripts/run.sh prod
```

脚本规则：

- 必须显式传入 `dev` 或 `prod`
- `./scripts/run.sh` 不传参数会直接报错
- 脚本会按自身位置定位仓库根目录，可在仓库根目录或 `scripts/` 目录执行
- 如需使用自定义环境文件，可手工执行 `APP_ENV_FILE=/absolute/path/to/custom.env python3.12 -m uvicorn app.main:app`

## API 列表

- `POST /admin/api/auth/login`
- `GET /admin/api/auth/profile`
- `POST /admin/api/auth/logout`
- `GET /admin/api/supervisor/hosts`
- `GET /admin/api/supervisor/services?host=127.0.0.1`
- `GET /admin/api/supervisor/services/{programName}?host=127.0.0.1`
- `POST /admin/api/supervisor/services`

## 跨域访问说明

- 所有 `/admin/api/*` 接口统一由服务端补齐 CORS 响应头
- 浏览器发送的 `OPTIONS` 预检请求会直接返回 `200`，不做 JWT 鉴权
- 当前策略对齐 `be-vita`：有 `Origin` 时原样回写，没有 `Origin` 时返回 `*`
- 跨域放行只解决浏览器可达性，不影响原有 `Authorization: Bearer <jwt>` 鉴权规则

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
    "username": "admin",
    "password": "Admin@123456"
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
    "user": "root"
  }'
```

查询服务列表：

```bash
curl 'http://127.0.0.1:18880/admin/api/supervisor/services?host=127.0.0.1' \
  -H 'Authorization: Bearer <access-token>'
```

查询服务详情：

```bash
curl 'http://127.0.0.1:18880/admin/api/supervisor/services/demo-project_member?host=127.0.0.1' \
  -H 'Authorization: Bearer <access-token>'
```
