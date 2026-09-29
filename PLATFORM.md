# 部署与运行

当前脚本用于双 Linux 节点实验，并非通用安装器。不要直接复用历史机器地址或已过期的租期。

## 启动条件与配置

两个节点需要 Python 3.10+ 和项目代码。Navigator 通过密钥认证 SSH 连接 Driver，使用固定目录 `/home/pair/AgentPair`。模型端点和模型名当前在 `agentpair/pair_worker.py` 中，部署前需检查并调整。

`deploy_platform.py`、`sync_platform.py` 和 `agentpair/bootstrap.py` 包含实验 IP、SSH 密钥路径；同步脚本还包含实验到期时间。部署前必须修改这些值。凭据经隐藏交互输入和 SSH 标准输入传递，不应写入源码或命令参数。

## 两种访问方式

默认工作台监听 `127.0.0.1:9090`，通过 SSH 转发访问并使用密码登录。

`sync_platform.py --public-demo` 启用公网免登录实验模式。当前 bootstrap 使用 8080 端口和固定公网 Origin。浏览器可以直接发布任务，不依赖用户电脑的 SSH 转发。Origin 和 CSRF 检查不能替代身份认证。

传入的 `expiresAt` 到期后拒绝新 POST 请求。此检查不终止已有工作，也不释放云资源；资源清理由独立实验进程执行。

## 任务 API

| 接口 | 用途 |
| --- | --- |
| POST /api/login | 登录模式下获取会话 |
| GET /api/session | CSRF token、费用估算和轮数限制 |
| GET /api/tasks | 任务列表 |
| GET /api/tasks/{id} | 对话、事件、结果 |
| POST /api/tasks | 发布任务，字段 title、message |
| POST /api/tasks/{id}/messages | 追加要求 |
| POST /api/tasks/{id}/cancel | 取消后续调用 |

写请求需要匹配的 Origin、JSON Content-Type 和 X-CSRF-Token；登录模式另需会话 Cookie。

API 的内部 `adapter=public_site` 与 `target` 用于有限网站查询，目标仅支持公网 IPv4 和标准 HTTP(S) 端口。Web 表单已隐藏内部选项，默认任务由 Navigator 规划天气工具或文字/代码建议，并未实现通用工具自动路由。

## 状态、费用与数据

单轮包含规划、执行、验收，最多追加一次执行与复核，即最多五次角色调用。结构化验收缺项不可通过，详情见 [决策机制](DECISIONS.md)。

默认 20 个任务、每任务 3 轮、消息 6000 字符、单轮 600 秒。取消不能保证立即中止在途请求。重启将活动任务标为中断，不自动重放付费调用。

当前工作台使用 `budget=None`，无模型总金额上限。费用仍按历史价格估算并记录预留；失败调用可能保留预留，所以不等于实际账单。单请求上下文和估算检查仍存在。

SQLite 数据库位于 Navigator 的 `runtime/tasks.db`。机器销毁前应导出记录，目前无自动异地备份。

## 过程展示

页面每两秒获取事件，展示当前阶段、角色交接、结果与验收依据。回放不调用模型。程序核验与模型判断分别标注，尚无逐 token 或逐命令执行直播。
