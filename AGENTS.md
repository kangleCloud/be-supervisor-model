# 仓库指南

## 全局约束

### 不保留历史兼容逻辑

当前项目处于 pre-v1.0 阶段，默认不为了历史实现保留兼容分支、兼容参数、兼容模式或临时兜底逻辑。

如果现有结构已经无法准确表达业务语义，应直接重构为更清晰的实现，而不是继续在旧逻辑上叠加补丁判断。

### 中文规范

新增或修改核心代码时，必须补充简洁中文注释，重点说明业务规则、异常边界、外部系统假设和关键流程原因。

所有 FastAPI 路由的 `summary`、`description`、`response_description`、请求模型字段说明、README 示例和接口文档说明统一使用中文。

所有面向调用方的接口返回文案统一使用中文；机器可读字段保持英文风格，例如 `code/msg/data`、`programName`、`configName`。

## 项目结构与模块组织

`app/` 是主 Python 包。

- `app/main.py`：应用工厂、异常处理、启动装配。
- `app/api/`：HTTP 路由与请求入口。
- `app/core/`：配置、响应、异常、日志、安全校验。
- `app/schemas/`：Pydantic 请求模型。
- `app/services/`：模板渲染、主机选择、配置文件、端口检查、Supervisor 编排。
- `app/executor/`：`local` / `ansible` 执行器。
- `app/templates/`：Jinja2 模板。
- `docs/`：业务流程说明与专题变更文档，采用 `NN.主题.md` 编号命名。
- `tests/`：`pytest` 测试。

## 文档约定

仓库业务文档统一放在 `docs/` 目录，采用专题文档制，而不是单文件变更台账。

- 基线业务说明固定放在 `docs/01.业务流程说明.md`
- 后续每次系统业务修改，都必须新增或更新 `docs/NN.主题.md`
- 如果修改影响系统主链路或角色职责，需要同步更新 `docs/01.业务流程说明.md`
- 单次专题变更新增一篇新的编号文档，不覆盖历史专题文档

后续专题文档固定包含以下章节：

- 背景与目标
- 当前问题或修改原因
- 业务规则变更
- 接口或配置变更
- 对调用方/运维的影响
- 验证方式

## 构建、测试与开发命令

本地开发使用 Python 3.12。

- `python3.12 -m venv .venv && source .venv/bin/activate`
- `pip install -r requirements.txt`
- `python -m compileall app`
- `pytest -q`
- `uvicorn app.main:app --host 0.0.0.0 --port 18880`

## 编码风格与命名规范

遵循 PEP 8，使用 4 个空格缩进。

- 模块、函数、变量使用 `snake_case`
- 类名使用 `PascalCase`
- 常量使用 `UPPER_SNAKE_CASE`

API 层只负责参数接收和响应封装；命令、文件系统和 Supervisor 业务逻辑应放在 `services/` 与 `executor/`。

## 测试规范

统一使用 `pytest`。

至少覆盖以下场景：

- 模板渲染与 INI 合法性
- programName / configName 生成
- 主机白名单校验
- 路径穿越与非法名称拦截
- 端口冲突扫描 `.ini/.ini.bak/.ini.bak.*`
- 备份、还原、原子写入
- API 响应结构统一
- `local` / `ansible` 执行器选择

## 提交与 Pull Request 规范

提交信息使用简短祈使句，例如 `Add supervisor port conflict checks`。

Pull Request 需要说明：

- 变更范围
- 配置项变化
- 已执行的验证命令
- 若涉及 API 变更，附上请求与响应示例

## 安全要求

- 目标主机必须来自配置白名单
- 禁止任意命令执行
- 禁止路径穿越
- 所有配置文件操作必须限制在 `/etc/supervisord.d`
- `programName/jobName/moduleName/configName` 必须满足安全正则
- 所有 shell 参数必须经过受控构造，不能直接拼接用户输入
- 默认不记录敏感凭证
