# AgentPair · AppLens 安全分析

**查看 AI 应用实际带入模型的内容，找到安全问题，并跟踪处理后的结果。**

AgentPair 提供 Driver 执行、Navigator 规划与复核的协作机制；AppLens 负责采集端点上的 AI 应用上下文。当前安全分析以 macOS、Windows 上的 WorkBuddy 为对象，将采集记录、具体证据、资产、处置和验证关联起来。

当前版本已实现两类威胁的本地自动检查，以及单项 AgentPair 模型复核。开放式的新威胁自动发现和实时分析事件流尚未实现。

## 当前能看到什么

| 功能 | 当前实现 |
| --- | --- |
| 模型数据 | 查看会话/调用记录、输入正文与内容分类；先加载列表，再按需读取选中请求 |
| 安全发现 | 按威胁类型展示受影响资产，再查看该资产上的具体发现 |
| 证据详情 | 用户消息、命中原文、消息角色、请求目标、采集时间及技术定位 |
| 分析视图 | 动态定位、原文高亮、证据连线、逐步查看、暂停和回放 |
| 处置管理 | 分派负责人、设置期限、提交处理结果、采集验证、人工关闭和复现重开 |
| AgentPair 复核 | 经明确发送范围确认，对选定发现的遮蔽证据进行分析与复核 |

### 两类安全威胁

**敏感凭据进入模型输入**：检查密码、私钥、密钥和令牌等内容。用户消息中的凭据与系统背景等消息中的凭据分别聚合，避免混淆一次输入和长期携带。同一设备的重复出现合并展示。

**无关项目背景进入模型输入**：当前只检测可靠提取的问候任务，例如“你好”，是否同时携带较长的其他项目背景。技术任务需要哪些背景尚不做通用判断，也不据此认定资料保密或未经授权。

服务器地址和账号保留用于辨认与修复；已识别的账户密码在证据页展示前缀、遮住最后四位。私钥及其他令牌使用完整遮蔽。提交给分析模型的凭据片段仍完整遮蔽，不发送页面显示的密码前缀。原始采集记录独立保留。

### 证据与动画的含义

应用生成的上下文记录不证明已经发送；捕获网络请求正文不证明模型服务保存、训练使用或有人滥用数据。页面分别显示这两种采集层的证据。

分析视图回放已完成的本地检测证据，核对正文 SHA256、消息路径和字符位置后绘制高亮。连线表示同一记录中的关联，不表示已经确认文件来源。当前不是实时 AgentPair 分析进度，也不展示模型内部思考。

## 从发现到修复

```text
采集记录 → 本地检查 → 威胁 / 资产 / 具体发现
                         ↓
                 分派负责人及期限
                         ↓
                    提交处理结果
                         ↓
                 新采集会话验证
                         ↓
            人工关闭；再次出现则重新打开
```

负责人提交结果后进入“待验证”，没有合格的新样本不能关闭。旧会话、重复记录、截断记录和不可信时间不计入验证；背景威胁仅检查新的问候会话。凭据威胁关闭前，还要求人工另行确认旧凭据已更换或撤销，平台不通过登录服务器验证密码。

分派是系统内记录，不会自动发送通知，也不会因为填写某个人的名字而授予其权限。当前安全管理员或设备所属账号可处置；访客和其他用户可全局查看。实际操作人员未采集到时明确标注，不由设备账号推断。

> 当前部署的全局审计及原始采集数据向访客开放。这是本项目的部署选择；含业务资料的数据在其他环境部署前，应按组织要求调整访问权限。

## AppLens 采集范围

当前接入 macOS、Windows WorkBuddy 的应用上下文记录，以及部分网络请求正文。输入中可识别的用户消息与背景资料具有原文定位；真正的 WorkBuddy 会话未关联时，采集请求标识不作为会话名称。

完整模型响应、独立工具执行、文件读取与进程/网络因果链尚未作为统一安全证据接入。Android 源码保留在仓库中，不代表已具备 Android WorkBuddy 或豆包真实模型请求采集能力。

## Driver 与 Navigator

| 角色 | 职责 |
| --- | --- |
| Navigator | 制定计划、检查证据和验收条件、决定返工或交付 |
| Driver | 按计划调用已接入工具或生成结果，返回工具证据 |

一般任务支持多轮对话、结构化复核、取消、历史记录及工具调用。云端协作和并行探索受资源配置与租约控制。模型复核不能代替原始证据，也不能自动关闭安全发现。

## 本地验证

Python 3.10 或以上版本。当前开发代码位于 `codex/agentpair-mvp` 分支：

```sh
git clone --branch codex/agentpair-mvp https://github.com/yardfribley-bit/agentpair.git
cd agentpair
python3 -m unittest test_credential_threats test_background_threats test_threat_workflow test_model_context -v
python3 -m unittest test_platform -v
python3 -m agentpair.demo
```

这些 Python 测试使用本地数据库、模拟后端和本机 HTTP 端口，不需要付费模型调用。`agentpair.demo` 是离线演示。浏览器测试使用 Playwright，仓库当前部分脚本包含开发机器的运行时路径，需要按本机环境调整。

生产启动、模型服务和云资源配置见 [PLATFORM.md](PLATFORM.md)。`ops/` 部署脚本包含本项目环境约定，并非通用一键安装器。运行配置、数据库、私钥及日志不随源码上传；录屏和安装包也未纳入此次源码提交。

## 运行页面

- [安全发现与分析视图](https://50.118.187.180/model-security)
- [模型数据](https://50.118.187.180/model-data?scope=global)
- [任务工作台](https://50.118.187.180/)

项目域名计划使用 `www.chuhaijian.com`；域名 HTTPS 配置尚未在本次工作中完成验证，以上使用已验证的 IP 地址。

## 文档与源码

| 内容 | 文档 / 目录 |
| --- | --- |
| 安全发现和处置流程 | [THREAT_MANAGEMENT_V1.md](THREAT_MANAGEMENT_V1.md) |
| 凭据检测与验证 | [CREDENTIAL_THREAT_V1.md](CREDENTIAL_THREAT_V1.md) |
| 无关背景检测范围 | [BACKGROUND_THREAT_V1.md](BACKGROUND_THREAT_V1.md) |
| 交互审计 | [INTERACTION_AUDIT_V1.md](INTERACTION_AUDIT_V1.md) |
| 单项安全复核 | [SECURITY_INVESTIGATION_V1.md](SECURITY_INVESTIGATION_V1.md) |
| 客户端 | [APPLENS_CLIENTS.md](APPLENS_CLIENTS.md)，`macos/`、`windows/`、`android/` |
| 服务端和页面 | `agentpair/`、`agentpair/web_assets/` |
| 协作机制与决策 | [DESIGN.md](DESIGN.md)、[DECISIONS.md](DECISIONS.md) |
| 部署及资源管理 | [PLATFORM.md](PLATFORM.md)、[RESOURCE_CONTROL.md](RESOURCE_CONTROL.md) |

后续重点：建立新采集任务的分析队列，使用 AgentPair 提出和核查新威胁候选，加入人工确认及回归样本；再将真实分析事件接入动态视图。自动分析的数据范围、外发许可和预算需要明确配置，不能把单项复核许可扩大为全部原文自动发送。
