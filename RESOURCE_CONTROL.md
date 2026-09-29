# Navigator 的 UCloud 资源控制

`agentpair.ucloud.UCloudClient` 封装允许的 UCloud 主机、镜像、价格和公网 IP 操作。API 密钥只由 Navigator 进程持有，不进入任务记录、模型上下文或前端响应。

`agentpair.resources.ResourceManager` 提供报价、创建有到期时间的 Driver、核验身份后释放和扫描到期租约。每个租约保存在权限为 0700 的目录；租约文件为 0600。默认限制为同时 4 台、单台最多 3600 秒、报价不超过 1 元/小时。实际限额必须按部署环境调整。

创建前必须配置镜像、区域、可用区、防火墙、机器规格和 SSH 公钥。创建请求只允许一台主机。返回实例 ID 后写入租约；请求失败时标记为 `reconcile_required`，释放时按精确名称查找，避免超时后遗留实例。释放只针对租约中的精确实例 ID，要求实例名称一致，并请求同时释放其公网 IP 和磁盘。到期回收由 `agentpair-reaper.timer` 每分钟触发，并独立于工作台服务运行。

目前已部署在持久 Navigator 服务器 `https://50.118.187.180/`。Navigator 规划结果的 `executionMode` 决定普通任务留在本机还是按需开通一台 UCloud Driver；程序会先报价、核对限额，随后按精确租约回收。模型只能提出 `local` 或 `cloud_driver`，不能调用完整 UCloud API，也看不到密钥。当前每个任务最多使用一台云端 Driver；多 Driver 并行调度尚未实现。
