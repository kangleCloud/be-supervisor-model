# be-supervisor-model

`be-supervisor-model` 是一个基于 FastAPI 的运维管理服务，用于统一管理白名单主机上的 Supervisor 配置、运行状态和初始化导入流程。

## 当前能力

- 登录鉴权：登录、查询当前用户、退出登录
- 主机管理：查询主机白名单、读取主机实时概况
- 服务管理：列表、详情、单服务同步、状态刷新
- 初始化导入：`GET staging` + `PRECHECK -> COMMIT`
- 服务变更：新增、修改、归档、还原、硬删除
- 运行操作：`start / stop / restart`

## 核心模型

- 主数据落在 MySQL `sys_supervisor_service`
- 远端现场固定为 `/etc/supervisord.d` 及其子目录
- 服务列表和详情默认只查数据库
- 远端状态和配置快照只通过显式接口同步
- 目标主机必须来自 `hosts` 白名单
- 远端执行统一通过 `local` 或 `ansible` 两类执行器

数据库真实列统一以这三组字段为主：

- `config_path`：相对 `/etc/supervisord.d` 的真实定位路径
- `file_name`：配置文件 basename
- `content_program_name`：配置内容 `[program:*]` 里的名称

## 配置

推荐范式：

- `config.yaml`：主配置
- `.env.dev` / `.env.prod`：敏感值

复制模板：

```bash
cp config.example.yaml config.yaml
cp .env.example .env.dev
cp .env.example .env.prod
```

关键配置项：

- `app.host` / `app.port` / `app.logLevel`
- `app.logPath`
  - 不配置时仅输出到 stdout
  - 配置后同时输出到 stdout 和滚动日志文件
- `database.*`
- `supervisor.confDir`
- `executor.inventoryPath` / `executor.remoteUser` / `executor.timeoutSeconds`
- `hosts`

环境变量优先级：

1. 进程显式环境变量
2. `.env.dev` / `.env.prod`
3. `config.yaml`
4. 代码默认值

环境文件选择规则：

- `APP_ENV_FILE=/absolute/path/to/custom.env`
- 或 `APP_ENV=dev|prod`

## 数据库迁移

应用启动只初始化 Tortoise 连接，不再自动执行 SQL migration。

首次初始化或模型变更后，统一执行：

```bash
APP_ENV=dev .venv/bin/aerich upgrade
```

新增 migration：

```bash
APP_ENV=dev .venv/bin/aerich migrate --name <message>
APP_ENV=dev .venv/bin/aerich upgrade
```

说明：

- `migrations/models/` 是唯一迁移来源
- `app/database/migrations/001_init_schema.sql` 只保留为历史结构快照
- 若数据库仍残留旧版 `program_name/config_name` 列或旧唯一键，启动会 fail-fast 并提示先执行 Aerich 升级

## 本地运行

创建虚拟环境并安装依赖：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

启动：

```bash
./scripts/run.sh dev
```

常用验证：

```bash
python3 -m compileall app tests scripts
.venv/bin/python -m pytest -q
```

## Docker 部署

仓库内置 `Dockerfile` 与 `docker-compose.yml`，默认只部署应用容器，继续连接外部 MySQL。

启动前要求：

- 宿主机已准备 `/etc/ansible`
- inventory 能按 `hosts[].ip` 直接匹配目标主机
- 宿主机可免交互访问目标主机
- 外部 MySQL 已建库并可连通

启动：

```bash
docker compose up -d --build
```

容器部署规则：

- 容器启动命令固定先执行 `aerich upgrade`
- 成功后再执行 `./scripts/run.sh prod`
- `APP_LOG_PATH=/var/log/be-supervisor-model/app.log`
- 宿主机日志目录固定为 `/data/logs/be-supervisor-model/`
- 应用日志固定为 `stdout + 文件双写`

## Ansible 前置条件

- `executorType=ansible` 的主机统一按 `host.ip` 作为 inventory pattern
- 控制机 `inventoryPath` 必须存在且可读
- 概况、导入、同步、增改删、运行操作都会复用同一套 ansible 配置
- 概况接口不允许前端回退到 mock 数据

## 关键 API 流程

认证：

- `POST /admin/api/auth/login`
- `GET /admin/api/auth/profile`
- `POST /admin/api/auth/logout`

Supervisor：

- `GET /admin/api/supervisor/hosts`
- `GET /admin/api/supervisor/overview`
- `GET /admin/api/supervisor/services`
- `GET /admin/api/supervisor/services/{contentProgramName}`
- `POST /admin/api/supervisor/services/{contentProgramName}/sync`
- `POST /admin/api/supervisor/services/status/refresh`

初始化导入：

- `GET /admin/api/supervisor/imports/staging?host=...`
- `POST /admin/api/supervisor/imports`
  - `mode=PRECHECK`
  - `mode=COMMIT`

服务变更：

- `POST /admin/api/supervisor/services`
- `PUT /admin/api/supervisor/services/{contentProgramName}`
- `DELETE /admin/api/supervisor/services/{contentProgramName}`
- `POST /admin/api/supervisor/services/{contentProgramName}/archive`
- `POST /admin/api/supervisor/services/{contentProgramName}/restore`
- `POST /admin/api/supervisor/services/{contentProgramName}/start`
- `POST /admin/api/supervisor/services/{contentProgramName}/stop`
- `POST /admin/api/supervisor/services/{contentProgramName}/restart`

## 文档索引

- `docs/01.业务流程说明.md`
- `docs/02.Supervisor管理表设计与同步流程.md`
- `docs/03.跨域预检与跨源访问说明.md`
- `docs/04.远端执行与路径安全约束说明.md`
- `docs/05.只读初始化导入与冗余字段设计.md`
- `docs/06.数据库基线DDL整合说明.md`
- `docs/07.Supervisor导入初始化API约定.md`
- `docs/08.Ansible输出兼容与告警治理.md`
- `docs/09.Supervisor服务列表分页与数据库查询改造.md`
- `docs/10.Supervisor归档与运行操作联动说明.md`
- `docs/11.Supervisor详情数据库化与单服务同步说明.md`
- `docs/12.Supervisor本地远端统一增改删说明.md`
- `docs/13.Supervisor服务器概况真实API说明.md`
- `docs/14.Supervisor初始化导入暂存恢复说明.md`
- `docs/15.Docker部署与日志输出说明.md`
