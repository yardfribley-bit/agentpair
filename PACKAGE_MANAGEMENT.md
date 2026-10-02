# 软件包目录与 Driver 缓存

用户入口 `/packages`；真实目录公开可见。管理员登记方案、预下载和下发安装。当前普通账号安装仍需管理员审批，不因页面新增而放开安装权限。

软件目录：Navigator `software-catalog.json`。Windows 方案必须固定版本、HTTPS 地址、SHA256 及 Inno/MSI 安装方式。Linux apt 方案单独标记，不冒充已缓存安装包。

缓存：常驻 Linux Driver 原生运行 `python -m agentpair.package_cache --directory /var/lib/agentpair/package-cache`，令牌通过 `AGENTPAIR_PACKAGE_TOKEN` 环境变量注入。监听 127.0.0.1:8092，需该节点 TLS 反向代理；没有 Docker。管理 API 要求令牌，下载路径按 SHA256 寻址。仅缓存无凭证的公开安装包；不要将内部或付费包放入该公开下载节点。

Navigator 配置 `/var/lib/agentpair/package-node.private.json`，权限0600：字段 `url`（Driver HTTPS origin）、`token`、`name`。URL 不含凭证；token 不进入页面或任务。未配置时页面明确显示未配置，仍可浏览目录并走官方来源安装。

下载进度与状态由 Driver manifest 回执提供：registered/downloading/cached/failed/interrupted/missing。只有 SHA256 匹配才原子发布 `.bin`，中断、错误及临时文件不作为可用缓存。每包上限 2 GiB，下载期限 15 分钟。当前需管理员监控磁盘空间，尚无自动清理、统计命中次数或多节点调度。

Windows 安装任务优先解析缓存 URL，保留官方 fallback URL，缓存不可达回退官方来源；两种来源均做 SHA256 与安装版本校验。**Windows 客户端需要更新安装脚本**才能使用 fallback，旧客户端遇缓存失败会报错。

已实现：软件列表、平台筛选、搜索、详情、缓存节点状态、管理员预下载、安装任务状态。历史多版本目录、apt 下载代理、清理操作及缓存命中统计未实现，页面不展示虚构数据。
