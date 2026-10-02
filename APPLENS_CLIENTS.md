# AppLens 三端构建与能力边界

AppLens 是端侧应用分析器，不是 Navigator 的协作 Driver。

三端通过 HTTPS 配对、上报基础信息及 AppLens v1 能力声明。协议定义在 `agentpair/applens_protocol.py`；不存在的采集能力必须报告 unsupported，不能将空列表解释为“没有发生行为”。

| 客户端 | 现有能力 | 暂缺 |
|---|---|---|
| Windows | 进程和应用清单、指定进程身份/TCP 快照、应用分析请求与结果回传 | 持续 ETW 进程、网络、文件事件 |
| Android | 配对、心跳、云端请求和手动任务回执 | 系统级进程/网络/文件采集 |
| macOS | 原生窗口、会话配对、进程清单同步、提交云端需求 | 自动结果回传、补采执行、应用清单、内核采集 |

这不是三端功能全量一致的完成声明。macOS 目前只在内存保留设备凭证，退出后需要重新配对；结果在平台查看。各平台需继续按统一状态机补齐结果回传和补采。

构建：Windows 运行 `windows/build.ps1`（GitHub Windows runner）；Android 运行 `python3 android/build.py`（SDK 36/JDK）；macOS 运行 `sh macos/build.sh`（Xcode 命令行工具）。Android 是开发签名 APK；macOS 未签名、未公证，不能作为正式发行包。

兼容现有下载链接，Windows/Android 安装包文件名暂保留 AgentPair，应用显示名为 AppLens。发布前须验证真实设备配对与网络交互，而不只验证编译。
