# Windows 真实网络应用试验 · 2026-10-01

## 范围

在已授权 UCloud Windows Server 2022 测试机安装官方 PuTTY 0.85，连接自有 Navigator `50.118.187.180:22`。不登录第三方主机、不扫描第三方网络。目标是进程身份与实时 TCP 快照，不是完整恶意行为审计。

## 已核验的真实证据

- 设备：`f4f38745d7a1868e0b6c1ff1`，Windows 主机 `10-9-126-87`。
- 本地分析：`b10e263c9c606d0b58f778f1245980b7`。
- 云端任务：`dda6e3dca2c74eaeb593543b320e8875`。
- 进程 PID：1444；启动时间：`2026-10-01T06:16:58.7751140Z`。
- 路径：`C:\Program Files\PuTTY\putty.exe`；签名状态：Valid。
- 程序 SHA256：`CAEC7C2D1FACA08285FEBB680F98D4920BEA97EFE857F95C5D2A4A3B6794A9E4`。
- TCP 采集时间：`2026-10-01T06:17:47.4315925Z`。
- Established：`10.9.126.87:49714 -> 50.118.187.180:22`。
- 同端口另有 Bound 记录；Bound 不等于 Listen。

Windows 客户端实际提交应用需求，服务端顺序下发 `process_details` 和 `process_tcp`，客户端执行并回传，云端模型返回中文报告。Windows 的 `C:\AgentPairNetworkTrial\collector.log` 已实际读取到该报告。

第二轮终态已在 Windows 日志实证：`Analysis state: completed / completed`。服务端已保存 2 条执行经验（进程身份、TCP 快照），不是完整安全分析方法已达到重复验证成熟度的声明。

## 实测发现并处理的问题

1. 临时脚本下载路由没有 `.ps1` MIME 类型，导致 502；补充处理后实际下载成功。
2. 关键词验收规则把报告的 DNS/负载采集范围限制当作必需证据缺失。补充结构化验收判定；明确 blockingGaps 仍拦截，已有全部验收检查通过时不再由范围限制关键词单独推翻。
3. 第一轮云端状态为 needs_more_evidence，不能宣称第一轮通过。修复后第二轮实际状态为 completed，Navigator verdict 为 pass，报告已正确区分 Bound 和 Listen、签名有效性与运行安全性。
4. 分析接口原先在云端结束后仍显示 reviewing；新增终态映射，客户端可看到 completed / needs_more_evidence / failed 等真实终态。

## 边界

Valid 签名不是安全证明。TCP 时间点快照没有 DNS、UDP/QUIC、流量负载、持续行为记录，不能据此断言没有敏感数据外传。此次使用更新后的 PowerShell 主机执行分析，不代表安装包 GUI 的全部交互能力已经通过验收。
