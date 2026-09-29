# 双节点对话工作台

本次 Navigator 为 106.75.9.169，Driver 为 106.75.18.16。一次性实验节点不是框架的固定组成部分。

Navigator 的 `agentpair.platform` 只监听127.0.0.1:9090。bootstrap 经 SSH stdin 接收模型令牌与工作台密码，内存传入进程，不写模型令牌文件。部署脚本仅更新现有节点，不创建机器。

本机 SSH 通道将127.0.0.1:18080转发到Navigator的127.0.0.1:9090。访问 http://127.0.0.1:18080/ ，用本机 `runtime/workspace-password.txt` 中的密码登录。密码文件权限受 umask 077 限制，已排除版本控制。勿放密码到URL中。SSH会话退出或租期释放后服务不可用。

## API

- POST /api/login：登录，设置HttpOnly / SameSite=Strict会话Cookie。
- GET /api/session：CSRF token、估算额度、轮数上限。
- GET /api/tasks 与 GET /api/tasks/{id}：任务和历史。
- POST /api/tasks：title、message、adapter、target。
- POST /api/tasks/{id}/messages：追加要求，启动下一轮。
- POST /api/tasks/{id}/cancel：取消；在途调用不保证即时中断，仍可能计费。

写请求检查Origin、JSON Content-Type、会话Cookie及X-CSRF-Token。任务文本不会作为shell命令执行。网站目标只支持公网IPv4与标准HTTP(S)端口，无URL凭据或查询参数，不支持任意域名，避免DNS重绑定与内网访问。

## 限制

20个任务，消息最多6000字符，每任务3轮，每轮3次调用，单轮10分钟。完整历史传给模型；超出估算上下文边界会失败，不偷偷截断或跨任务拼接。两个角色暂均使用deepseek-v3.2。

额外模型估算额度0.90元，每次调用先预留0.10元；成功回包后，按该请求的历史价格估算上界结算未用预留。未知或失败调用保留整笔预留，结算键持久化以避免重复退还。此前三次调用另有估算约0.049元。仅在历史价格不变假设下控制估算总量，不是供应商硬额度。SQLite保存额度，重启不重置。

Navigator持久数据在runtime/tasks.db，租期清理前应导出需要的记录；实验尚无异地自动备份。

## 动态协作

阶段来自stage_started、stage_completed和handoff_requested，页面每2秒同步。交接请求代表上下文已交给执行后端，不代表对端网络接收确认。动画解释角色和方向，不模拟模型内部推理或逐token输出。

显式回放不触发模型或工具调用。公网快照始终标注历史模式。
