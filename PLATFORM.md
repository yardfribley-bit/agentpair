# 部署与运行

持续运行的 Navigator 已部署在用户指定的 `50.118.187.180`；旧的双节点实验脚本仍保留，并非通用安装器。不要直接复用历史机器地址或已过期的租期。

## 持久 Navigator 与按需 Driver

工作台：<https://50.118.187.180/>，HTTPS 证书和密码登录。服务由 `agentpair-navigator.service` 运行，Nginx 反向代理到 `127.0.0.1:9090`。证书续期由 `agentpair-certbot.timer` 处理。UCloud 凭据仅在服务器 `/etc/agentpair/private.json`，文件仅服务账户可读，不能放进 Git 或模型提示词。

Driver 默认在 Navigator 节点本地执行。只有用户指定云端工程方法才由 `CloudDriverBackend` 在 `cn-bj2` 分配 Ubuntu Driver：云端结对一台、并行探索两台，优先复用现有租约，通过限源 IP 防火墙和专用 SSH 密钥连接。每台最多租一小时；独立的 `agentpair-reaper.timer` 每分钟检查到期租约。详见 [资源控制](RESOURCE_CONTROL.md)。

此部署的代码同步入口为 `sync_persistent_navigator.py`，它包含特定服务器地址和密钥路径，仅供该实例维护。若服务器重建，须重新核验 SSH 主机身份和密钥；不要跳过校验。

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

单轮先规划、执行、验收；若验收未通过且给出补正意见，最多再整改两轮并复核。超过整改上限仍未通过会保留 blocked 状态与验收意见，不会假报完成。结构化验收缺项不可通过，详情见 [决策机制](DECISIONS.md)。

默认 20 个任务、每任务 3 轮、消息 6000 字符、单轮 600 秒。取消不能保证立即中止在途请求。重启将活动任务标为中断，不自动重放付费调用。

当前工作台使用 `budget=None`，无模型总金额上限。费用仍按历史价格估算并记录预留；失败调用可能保留预留，所以不等于实际账单。单请求上下文和估算检查仍存在。

SQLite 数据库位于 Navigator 的 `runtime/tasks.db`。机器销毁前应导出记录，目前无自动异地备份。

## 过程展示

工作台顶部的实时资源面板通过 `/api/resources` 每 5 秒刷新：统计已返回的生成/决策调用 token、每任务 token、活动任务、平台 Driver 租约和剩余时间，以及 Navigator 的内存、系统负载、磁盘占用。token 来自供应商返回用量；失败或未返回调用可能未计入。云机按租约报价、整小时向上取整估算，不等于供应商账单；仅覆盖本平台记录。余额、模型实际费用及 Navigator 固定费用尚未接入，显示未知。接口不返回密钥或私有配置。

页面每两秒获取事件，展示当前阶段、角色交接、结果与验收依据。回放不调用模型。程序核验与模型判断分别标注，尚无逐 token 或逐命令执行直播。
