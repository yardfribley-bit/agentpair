# AgentReins v2（macOS 首版）

独立命令 `agentreins_v2`。读取 AppLens 已捕获的 WorkBuddy 请求和 WorkBuddy 本机执行记录；不会替换旧采集器。Windows 暂不开发。

在仓库根目录执行：

```sh
export PATH="$PWD/bin:$PATH"
agentreins_v2 collect --since 2026-10-03T13:10:00Z
```

另开终端，使用相同数据目录：

```sh
agentreins_v2 serve
```

打开终端打印的本机地址（包含访问令牌）。默认数据目录 `~/.agentreins_v2`。用 `--data-dir /你的目录` 放在子命令前覆盖。页面是首版证据查看器，尚未完成已讨论的动态执行链路产品界面。

## 目前实际采集什么

- 增量读取 AppLens `workbuddy-network.jsonl`；校验请求正文哈希，保存模型输入、用户任务、工具指令和已进入后续请求的工具结果。
- 读取新增或变化的 `~/.workbuddy/traces/*/*.json`；保留模型响应、工具参数和返回、时间、原生 trace/span/session 标识。
- 单独标记工具返回中的显式失败，即使外层执行状态显示成功。
- 对记录里引用的文件保存当前大小和修改时间。它是采样时的文件状态，不能证明历史读写。
- 可选 `collect --process-snapshots`：每五秒采样 WorkBuddy 及子进程的 PID、父子关系、RSS、可执行文件和 macOS Seatbelt 状态。不采集进程命令行或环境变量。

## 流式存储与恢复

队列同时限制批次数和字节数；SQLite WAL 持久化事件和源游标。相同事件不重复入库，模型上下文按消息内容复用，正文和显示元数据分开存储。请求还原保持 JSON 内容语义，不保留原始 HTTP 字节序列。

队列暂满时保留游标等待重试；单批超限或持久化失败明确报错，不跳过来源。日志轮转产生覆盖变化记录。默认队列 16 MiB、存储逻辑预算 512 MiB；预算不是操作系统磁盘硬配额，SQLite 页、WAL、回滚后遗留正文会使物理占用不同。文件以本机私有权限保存，不自动上传。

## 当前边界

本版消费已有采集日志，并非新的网络拦截器。尚无原生文件读写事件、网络连接事件、完整内存变化或 VideoGen 内部服务采集。

网络侧用户任务按问题历史生成投影标识，不等于 WorkBuddy 原生会话。trace 与网络尚未完成跨来源关联；不同记录不会仅凭时间邻近拼成一条执行链。相同问题历史可能合并，不能据此统计独立会话数。

本机查看器用轮询刷新、按任务筛选、展开原文；后台采集需独立运行。不是已完成的动画原型。

```sh
agentreins_v2 status
agentreins_v2 events --limit 30
agentreins_v2 collect --once
python3 -m unittest test_agentreins_v2 -v
```

## 网络采样与覆盖核对

```sh
agentreins_v2 collect --since 2026-10-03T13:10:00Z --network-snapshots
agentreins_v2 audit
```

`--network-snapshots` 自动启用进程采样，每五秒用 `lsof -nP -a -p ... -i` 查询 WorkBuddy 及其子进程持有的网络套接字，保存进程实例、协议、本地/远端地址端口和状态。不解析域名，不抓网络正文，不记录收发字节数。权限不足、采样失败或缺少进程范围会记录采集缺口。

连接由 PID 与同轮进程采样关联；五秒内的短连接可能漏掉，PID 复用和进程退出存在竞态，因此不据此证明工具调用的因果关系。当前没有工具调用到网络连接的确定关联，也不能把所有远端连接都标成出站连接。

`audit` 遍历已存储记录、验证模型请求内容能否还原，并列出覆盖缺口。完整还原一份已捕获请求，不代表所有模型请求都被拦截到；报告保持 `all_model_requests_captured: unknown`。trace 的 generation 数量不能直接作为 HTTP 请求数量的分母。

## 执行关系图与原生采集计划

`agentreins_v2 graph` 输出证据节点、关联边和缺失关系。工具调用与返回采用同来源的原生 `tool_call_id`；返回进入后续模型请求采用调用标识、消息正文指纹和保存的消息顺序核对。每次后续请求中的重复上下文均保留包含关系。

文件引用与事后文件状态、采样进程与套接字只标记 `partial`。工具到进程、工具到网络缺少原生标识时明确列出断点，不用时间相近猜测关联。

macOS 下一步必须采用事件源：进程和文件由 Endpoint Security 事件接入，网络短连接由 Network Extension 网络流事件接入；现有五秒采样仅补充资源状态。系统事件带来的进程身份与工具执行边界需要传递或观测到原生调用关联，共享服务不能把所有活动归入同一工具。

本机 `eslogger` 事件类型检查通过，但实际启动返回 `ES_NEW_CLIENT_RESULT_ERR_NOT_PRIVILEGED`；需要管理员身份，并按系统要求授予完全磁盘访问。自研 Endpoint Security / Network Extension 客户端另需相应签名与 entitlement，当前未提供已签名原生扩展。`uipc_connect` 是 Unix 域套接字事件，不能替代 TCP/UDP 短连接采集。

验收应包括短时间连接后立即退出、短命进程读写文件、PID 复用、并行工具、共享工具服务和事件丢失：资源动作都应显示来源、所属执行或未关联原因。事件型采集也需记录源丢失和队列背压，不能承诺绝不漏采。
