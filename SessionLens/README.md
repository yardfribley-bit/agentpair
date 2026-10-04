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

单原始记录超过 16 MiB 时保留游标并显示错误；单事件超过上报配额时保留本地、显示数量，并继续上传其他记录，不伪造成功。尚未实现大事件分片上传。工具往返通过 callId 关联，不能仅凭会话日志证明真实系统行为。重复来源记录保留各自证据，不把记录数当作用户操作数。

## 构建与验证

GitHub Actions `.github/workflows/sessionlens-desktop.yml` 在 macOS、Windows 本机 runner 上测试并打包，产物为 macOS `.app` 与 Windows 便携目录（包含 `.exe` 及依赖）。桌面构建尚未签名或公证。

```sh
python -m unittest discover -s tests -v
python desktop_main.py --self-test
python -m PyInstaller --noconfirm --windowed --name SessionLens desktop_main.py
```

原标准库命令 `python -m sessionlens --source 路径 --state runtime/collector.db` 保留用于 Codex 单目录采集；双来源桌面运行使用上面的入口。

## 分析链路

`sessionlens.analyze`、`sessionlens.report_site` 保留已有分析和展示实现。AgentPair SessionStore 接收 Codex、WorkBuddy，校验设备身份、回执和重放一致性；按 callId 组织证据。模型分析仍是明确标记覆盖范围的摘录，不是全会话自动分析。会话洞察展示与原始日志上报接口分开授权。

运行数据库、日志、令牌、虚拟环境和构建产物不提交 Git。
