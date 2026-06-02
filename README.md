# be-supervisor-model

`be-supervisor-model` 是一个基于 FastAPI 的运维管理服务，用于集中管理目标主机上的 Supervisor 配置文件和进程状态。

它面向运维后台或后续前端管理系统，提供主机查询、服务配置新增/修改/删除、备份还原、端口冲突检测，以及 `supervisorctl` 常用命令的 REST API。

## 技术栈

- Python 3.12
- FastAPI
- Pydantic
- Jinja2
- PyYAML
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

## 配置

复制示例配置：

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

关键配置项：

- `APP_HOST` / `APP_PORT`
- `SUPERVISOR_CONF_DIR`
- `COMMAND_TIMEOUT_SECONDS`
- `EXECUTOR_TYPE`
- `ANSIBLE_INVENTORY_PATH`
- `API_TOKEN`

主机列表示例见 [config.example.yaml](/Users/zhuningkang/Documents/git/github/supervisor-model/be-supervisor-model/config.example.yaml:1)。

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

- `GET /api/supervisor/hosts`
- `GET /api/supervisor/services?host=127.0.0.1`
- `GET /api/supervisor/services/{programName}?host=127.0.0.1`
- `POST /api/supervisor/services`
- `PUT /api/supervisor/services/{programName}`
- `DELETE /api/supervisor/services/{programName}?host=127.0.0.1&deleteBackup=false`
- `POST /api/supervisor/services/{programName}/start`
- `POST /api/supervisor/services/{programName}/stop`
- `POST /api/supervisor/services/{programName}/restart`
- `POST /api/supervisor/services/{programName}/backup`
- `POST /api/supervisor/services/{programName}/restore`
- `GET /api/supervisor/ports/check?host=127.0.0.1&port=9001`
- `POST /api/supervisor/reread`
- `POST /api/supervisor/update`
- `GET /api/supervisor/status?host=127.0.0.1`

统一响应格式：

```json
{
  "code": 200,
  "msg": "success",
  "data": {}
}
```

## 示例

新增服务：

```bash
curl -X POST http://127.0.0.1:18880/api/supervisor/services \
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
    "priority": 999,
    "autostart": true,
    "autorestart": true,
    "autoStart": false
  }'
```

修改服务并自动重命名：

```bash
curl -X PUT http://127.0.0.1:18880/api/supervisor/services/demo-project_member \
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
    "priority": 999,
    "autostart": true,
    "autorestart": true,
    "autoStart": false
  }'
```

删除服务：

```bash
curl -X DELETE 'http://127.0.0.1:18880/api/supervisor/services/demo-project_member?host=127.0.0.1&deleteBackup=false'
```

备份配置：

```bash
curl -X POST http://127.0.0.1:18880/api/supervisor/services/demo-project_member/backup \
  -H 'Content-Type: application/json' \
  -d '{"host":"127.0.0.1"}'
```

还原配置：

```bash
curl -X POST http://127.0.0.1:18880/api/supervisor/services/demo-project_member/restore \
  -H 'Content-Type: application/json' \
  -d '{"host":"127.0.0.1"}'
```

检测端口冲突：

```bash
curl 'http://127.0.0.1:18880/api/supervisor/ports/check?host=127.0.0.1&port=9001'
```

## 生产安全建议

- 目标主机必须通过配置白名单声明，不允许调用方传入任意目标
- 严格限制 `sudoers` 和提权代理只允许执行受控命令
- `ansible` 节点需要最小权限访问 Inventory 与 SSH 凭据
- 不要把生产 Token、Inventory、SSH 密钥提交到仓库
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
curl http://127.0.0.1:18880/api/supervisor/hosts
```

```bash
curl 'http://127.0.0.1:18880/api/supervisor/ports/check?host=127.0.0.1&port=9001'
```
