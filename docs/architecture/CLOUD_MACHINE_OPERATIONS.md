# 平台云机器操作

管理员入口：`/cloud-machines`。浏览器和自动化客户端共同调用 Navigator HTTP API；云厂商凭证留在服务端，不交给浏览器。

## 操作流程

1. 选择 Windows Server 2022（2 核 4 GB）或 Ubuntu 22.04（1 核 1 GB），查询实时报价。
2. 确认价格后创建。创建请求带唯一 `requestId`，网络超时后使用同一 ID 查询/重试，避免重复收费。清理定时器未运行时拒绝创建。
3. 查看云端状态和公网地址；检查 SSH 实际身份。Windows 验证 Administrator，Linux 验证 pair。云端 Running 不等于登录成功。
4. 选择软件方案并安装。Windows 使用 PowerShell、固定 HTTPS 安装包与 SHA256、静默安装及注册表版本校验；Linux 使用 apt 与 dpkg 版本回执。
5. 查询安装操作。只有验证回执通过才是 completed；超时或服务重启而缺回执为 interrupted，需要核验目标机，不能直接当成成功。

Windows RDP 允许公共来源，SSH 仅 Navigator 来源；管理员可查看 RDP 连接凭证。Linux 使用密钥登录。安装不需要 Docker。

## 共用接口

所有操作要求管理员会话；POST 还要求 Origin 和 CSRF 校验。

| 接口 | 用途 |
| --- | --- |
| POST /api/cloud/quote | 报价，system |
| POST /api/cloud/create | 创建，system/maxHourlyCNY/requestId |
| GET /api/cloud/machines | 本功能管理的租约列表 |
| GET /api/cloud/machines/{id} | 核验真实主机状态 |
| POST /api/cloud/start | 启动有效租约的已停止机器，leaseId |
| GET /api/cloud/machines/{id}/login | 实际 SSH 登录核验 |
| GET /api/cloud/machines/{id}/connection | 管理员连接信息 |
| POST /api/cloud/install | 安装，leaseId/softwareId |
| GET /api/cloud/operations/{id} | 安装状态与证据 |

Python 自动化入口：`agentpair.cloud_client.CloudClient`。先 HTTPS 管理员登录；调用 create 前生成并保存 request_id，失败重试时不得换 ID。该客户端不直接调用 UCloud 或 SSH。

## 验证边界

当前测试覆盖模拟云报价、创建幂等、价格上限、失败资源记录与安装回执验证。没有在本轮创建机器，Windows/Linux 真机开机—登录—安装验收仍待下一轮。Windows 被占用日志的采集回归已加入 Windows CI 流程，本地 macOS 未运行 PowerShell 测试。
