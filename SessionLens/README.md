# SessionLens

独立桌面会话日志采集器，支持 macOS、Windows 上的 Codex 和 WorkBuddy。与 AppLens 同级：SessionLens 读取会话日志，AppLens 负责请求上下文；最终由 AgentPair 处理分析。

## 桌面版

在本目录执行：

```sh
python -m pip install -r requirements-desktop.txt
python desktop_main.py
```

点击“设置并开始采集”，选择 Codex、WorkBuddy 或同时采集；应用自动适配操作系统，不需要平台切换。界面分别展示两个来源的用户提问、Agent 回复、解题思路、工具调用、工具返回、会话背景、用量与状态、暂未识别记录。最近内容有可读摘要，双击查看完整原文、会话 ID、工具 callId、文件位置、字节范围和哈希。

采集过程显示文件读取、历史回填字节进度、本地保存、未确认上报记录、发送状态和平台接收数量。所有数量来自本机数据库，未配置上报时明确显示仅本地采集。关闭应用停止采集；重新打开后通过设置启动，接着上次游标继续读取。

默认读取所有历史文件，不设置时间或文件数量筛选：

- Codex：`~/.codex/sessions`、`~/.codex/archived_sessions` 的 rollout JSONL。
- WorkBuddy：`~/.workbuddy/projects` 的 JSONL；设置中可更改目录。

只有源日志实际提供的 reasoning 才会被采集；这不是隐藏思维链、进程、网络或完整模型输入的拦截器。源文件不存在时文件计数为零，不生成模拟记录。日志格式未知时保留原文并归入暂未识别。

## 存储与上报

macOS：`~/Library/Application Support/SessionLens/collector.db`。
Windows：`%LOCALAPPDATA%\SessionLens\collector.db`。
配置在同目录 `settings.json`，不保存设备令牌。桌面单实例锁防止重复启动；SQLite WAL 保存记录、读取游标和按上报目的地/令牌身份隔离的回执。

不配置接口和令牌，仅本地保存。启用上报需要 AgentPair 登记的设备令牌和 HTTPS 地址，例如：

```text
https://www.chuhaijian.com/api/sessionlens/events
```

启用后发送所选日志的完整记录，不自动脱敏；设置界面明确说明上传范围。采集与上报使用独立线程，失败自动退避；只有平台完整确认这一批事件 ID 后才标记接收。日志目录每 15 秒发现新文件，已发现文件约每 0.5 秒检查增量；每文件单轮读取 200 行，按修改时间优先处理。实际延迟还受日志落盘、历史积压和磁盘性能影响。

单原始记录超过 64 MiB 时保留游标并显示错误；单事件超过上报配额时保留本地、显示数量，并继续上传其他记录，不伪造成功。尚未实现大事件分片上传。工具往返通过 callId 关联，不能仅凭会话日志证明真实系统行为。重复来源记录保留各自证据，不把记录数当作用户操作数。

## 构建与验证

GitHub Actions `.github/workflows/sessionlens-desktop.yml` 在 macOS、Windows 本机 runner 上测试并打包，产物为 macOS `.app` 与 Windows 便携目录（包含 `.exe` 及依赖）。桌面构建尚未签名或公证。

```sh
python -m unittest discover -s tests -v
python desktop_main.py --self-test
python -m PyInstaller --noconfirm --windowed --name SessionLens desktop_main.py
```

原标准库命令 `python -m sessionlens --source 路径 --state runtime/collector.db` 保留用于 Codex 单目录采集；双来源桌面运行使用上面的入口。

## 分析链路

`sessionlens.analyze`、`sessionlens.report_site` 保留已有分析和展示实现。AgentPair SessionStore 接收 Codex、WorkBuddy，校验设备身份、回执和重放一致性；按 callId 组织证据。正式接收接口部署脚本为 `ops/deploy_receiver.py`，复用 AgentPair 设备认证、带备份与回滚。模型分析仍是明确标记覆盖范围的摘录，不是全会话自动分析。会话洞察展示与原始日志上报接口分开授权。

运行数据库、日志、令牌、虚拟环境和构建产物不提交 Git。

### 任务监督与历史追溯

桌面首页按已确认原型提供三栏：任务列表、原始要求与处理经过、对应证据。
监工模式持续更新各会话最近的任务；历史追溯按提问、文件和工具记录中的关键词查找任务，支持 Codex / WorkBuddy 筛选。
点击步骤核对原文、文件来源及字节位置，可以在本机标记待核实并导出完整记录。

任务索引在后台增量建立，首次升级会整理已有历史，原始采集数据不会删除。
大任务分批展开步骤。任务归属按同一会话内的用户提问边界建立，相邻重复提问记录合并；缺少日志的行为无法补推。
“结束状态未记录”与“已记录结束”分别显示；监工展示日志最新动作，不证明 Agent 进程仍在运行。
摘要来自记录摘录，未记录的处理理由不自动生成。采集和上报配置入口位于右上角，未配置接口与令牌时仅本地保存。

监工模式默认跟随最新任务与动作，点击证据可取消跟随，勾选“跟随最新动作”恢复。
普通启动自动开始本地采集，上报仍需明确配置接口与本次令牌。
WorkBuddy 提问从 `user_query` 中提取，系统提醒与历史压缩摘要不作为新任务；工具调用与返回按调用编号成对核对。
历史整理按小批次节流（每次 50 条，批次间等待 0.5 秒），新记录使用独立优先通道；启动先为两种 Agent 预热近期记录。

### DeepSeek 任务助手

选中一个任务，在“问当前任务”中输入问题并点击“问 DeepSeek”。桌面只在提问时发送所选任务的证据片段，调用 AgentPair 的规划、分析、复核流程；使用既有 `aigc.gether.net` 中转 `deepseek-v3.2`。分析在后台运行，采集和历史整理继续进行。

回答解释用户要求、日志中记录的处理依据、工具关键参数、结果与调整，每段引用可点击核对本机原文。明确区分直接记录、推断与未知；当前是选定任务问答，不是全历史语义检索。超过 120 条的任务选取首尾片段，长正文会截取，界面显示覆盖范围。原文保持完整。

分析结果缓存在本机 `assistant.db`；同一证据版本与问题不会重复付费调用。已有回答可以再次打开，任务有新记录时提示重新分析。

`ops/deploy_assistant.py` 部署私有 HTTPS 网关并配置当前 Mac；中转密钥只保留在 AgentPair 服务器。客户端持有仅供分析网关使用的授权文件（不含服务器登录信息或中转主密钥）。Windows 可在设置中配置同一服务 URL 与管理员发放的分析授权文件。网关的模型分析不提供命令执行工具。
