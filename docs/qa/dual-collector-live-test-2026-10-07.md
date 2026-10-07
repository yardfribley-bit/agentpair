# 双采集器实时正向测试

用户在桌面 WorkBuddy 发送标记 SL-20261007-A，Agent 回复“收到。”。测试会话 b2ea545b-1ce3-466b-8825-d2ca1e7d8ed5。

- AppLens 平台收到 4 份相关记录：2 份应用上下文带真实会话标识，2 份网络上下文以 flowID 标识。不能将 4 份记录当成 4 次独立模型交互。
- SessionLens 首次未发现新文件，后来本地采到但未上报。定位历史扫描整轮延迟、新记录排在历史后，以及 Codex 高频记录挤占 WorkBuddy 上传。
- 已修复：扫描期间周期性重新发现新文件；索引批次移到扫描轮末；上传在启用采集源之间轮转，同时交替新记录与旧记录。批次 5 条、成功后等待 10 秒保持，历史游标和回执不重置。
- 已备份、构建并安装本机 Intel SessionLens。备份 /private/tmp/SessionLens-before-realtime-fix.app。
- 复验平台收到该会话 5 条 SessionLens 记录：用户消息、助手消息、reasoning、file_snapshot、session_metadata。标记消息与 AppLens 应用上下文的真实会话标识一致，成为正向候选样本。
- 调度与基础流水线 9 项测试通过。

本测试为单轮标记任务，证明双采集与同会话证据具备基础；不代表多轮需求语义聚合已经上线或全量无漏采。网络 flowID 与真实会话之间仍需独立关联证据。
