# be-supervisor-model

`be-supervisor-model` 是一个基于 FastAPI 的运维管理服务，用于统一管理目标主机上的 Supervisor 配置。

当前版本包含两类能力：

- 登录鉴权：登录、查询当前用户、退出登录
- Supervisor 管理：查询主机、查询纳管服务、查询服务详情、执行初始化导入，并仅对本地主机新增服务

Supervisor 配置主数据落在 MySQL 8 的 `sys_supervisor_service` 表中；远端 `/etc/supervisord.d` 及其子目录中的 `*.ini` 是实际生效结果；运行状态实时来自 `supervisorctl status`。

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
- `ansible`：当需要管理远程主机时，通过 `ansible` ad-hoc 命令执行只读配置读取与状态查询

配置文件读取来源取决于执行器与部署位置：

- `local` 场景下，配置文件直接从本机读取
- `ansible` 场景下，配置文件通过远程服务器读取

当前项目强约束：

- 远端 `ansible` 主机只允许读取配置和查询状态
- 禁止通过服务端修改远端配置文件
- 禁止通过服务端触发远端 `supervisorctl start/stop/restart/reread/update`
- 允许通过导入 API 或导入脚本只读扫描远端配置并写入数据库，但绝不修改服务器现场

配置文件默认目录：

```text
/etc/supervisord.d
```

仅允许操作：

- `*.ini`
- `*.ini.bak`
- `*.ini.bak.*`

主数据字段语义：

- `configPath`：相对 `/etc/supervisord.d` 的真实配置路径，作为现场定位键
- `fileName`：配置文件 basename
- `contentProgramName`：配置内容 `[program:*]` 中的名称
- `configName`：兼容字段，固定等于 `fileName`
- `programName`：兼容字段，固定等于 `contentProgramName`
- `manageMode`：`TEMPLATE_MANAGED` 或 `IMPORTED_READONLY`

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

当前推荐配置范式：

- `config.yaml`：承载主配置
- `.env.dev`：开发环境敏感覆盖项
- `.env.prod`：生产环境敏感覆盖项

复制配置模板：

```bash
cp config.example.yaml config.yaml
cp .env.example .env.dev
cp .env.example .env.prod
```

`config.yaml` 负责的主配置：

- `app.host` / `app.port` / `app.logLevel`
- `database.host` / `database.port` / `database.name` / `database.user`
- `auth.accessTokenExpireMinutes`
- `supervisor.confDir` / `supervisor.commandTimeoutSeconds`
- `executor.type` / `executor.inventoryPath` / `executor.remoteUser` / `executor.timeoutSeconds`
- `hosts`

`.env.dev` / `.env.prod` 负责的敏感覆盖项：

- `DATABASE_PASSWORD`
- `JWT_SECRET`
- `APP_CONFIG_PATH`

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
- `APP_ENV` 不依赖写在 `.env.dev/.env.prod` 内，应该由 `./scripts/run.sh dev|prod` 或外部环境显式传入

路径规则：

- `SUPERVISOR_CONF_DIR` 等绝对路径配置只做词法归一化，不做宿主机物理 `resolve()`
- 例如 `/etc/supervisord.d` 会保持 `/etc/supervisord.d`，不会在 macOS 控制机上被改写成 `/private/etc/supervisord.d`

主机列表示例见 [config.example.yaml](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/config.example.yaml:1)。

数据库启动规则：

- 服务启动时会自动创建目标数据库（若不存在）
- 会自动执行 `app/database/migrations/001_init_schema.sql` 单基线初始化 SQL
- 会自动初始化一条超级管理员账号：`admin / Admin@123456`
- 其他账号不提供 HTTP 创建接口，需要运维手工插入 `sys_user`

密码哈希可以使用脚本生成：

```bash
python3 scripts/hash_password.py
```

## 导入现网配置

现网已有 Supervisor 配置时，前端应优先调用初始化导入 API，再使用“库为主”的服务列表和详情接口。

初始化导入 API：

```bash
curl -X POST http://127.0.0.1:18880/admin/api/supervisor/imports \
  -H 'Authorization: Bearer <access-token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "host": "10.1.0.104",
    "mode": "DRY_RUN"
  }'
```

接口规则：

- `mode=DRY_RUN`：只返回逐文件预检结果，不写 `sys_supervisor_service`
- `mode=APPLY`：同步执行正式导入，返回 `IMPORTED/UPDATED/SKIPPED` 明细
- 固定递归扫描 `/etc/supervisord.d` 下全部 `*.ini`
- 固定排除 `.ini.bak` 和 `.ini.bak.*`
- 响应 `summary` 固定包含 `planned/imported/updated/skipped`
- 响应 `items` 固定按 `configPath` 升序返回，便于前端稳定展示

命令行脚本保留为运维兜底入口：

```bash
.venv/bin/python scripts/import_supervisor_services.py --host 10.1.0.104 --recursive
```

导入规则：

- 默认是 `dry-run`，只有追加 `--apply` 才会写数据库
- 支持 `--host` 指定单台主机
- 支持 `--recursive` 递归扫描 `/etc/supervisord.d` 子目录
- 只导入 `*.ini`
- 不导入 `.bak` 和归档备份
- `fileName` 和 `contentProgramName` 会原样冗余保存，即使两者不一致也不会互相覆盖
- legacy 配置允许重复 key，按最后一个值生效，并在 `parseWarnings` 中记录
- 结构化字段能提取就填，提取不到就留空；只要能识别为单个合法 `[program:*]` 段就允许入库
- 导入脚本允许读取远端主机现有配置，但不会修改远端文件

## 业务文档

- 基线业务说明见 [docs/01.业务流程说明.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/01.业务流程说明.md)
- 本次主数据化改造见 [docs/02.Supervisor管理表设计与同步流程.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/02.Supervisor管理表设计与同步流程.md)
- 本次跨域预检修复见 [docs/03.跨域预检与跨源访问说明.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/03.跨域预检与跨源访问说明.md)
- 本次远端只读约束与路径修正见 [docs/04.远端只读约束与路径语义修正.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/04.远端只读约束与路径语义修正.md)
- 本次只读初始化导入与冗余字段设计见 [docs/05.只读初始化导入与冗余字段设计.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/05.只读初始化导入与冗余字段设计.md)
- 本次数据库基线 DDL 整合见 [docs/06.数据库基线DDL整合说明.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/06.数据库基线DDL整合说明.md)
- 本次 Supervisor 导入初始化 API 约定见 [docs/07.Supervisor导入初始化API约定.md](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/docs/07.Supervisor导入初始化API约定.md)

## 启动方式

项目默认在 `.venv` 环境中运行；开发、测试和手工启动都以 `.venv` 作为标准 Python 运行时。

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
- `POST /admin/api/supervisor/imports`
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

远端主机限制：

- 当 `host` 对应 `ansible` 主机时，`POST /admin/api/supervisor/services` 会直接返回 `403`
- 返回文案：`当前项目禁止修改远端配置文件`

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
