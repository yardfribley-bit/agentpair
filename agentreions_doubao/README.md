# agentreions_doubao

本机豆包视频制作可视化工作台。把用户需求、需求变化、素材、制作参数、实际工具调用、返回结果和产物按证据串联，支持实时读取和历史回放。

这是独立桌面应用。采集、SQLite 存储与展示都在本机；没有 AgentPair 上传器、云端分析接口或遥测服务。豆包自身的生成服务仍按豆包正常工作方式运行。

## 已验证的真实场景

2026 年 10 月 9 日完成 Mac 本机文生视频、图生视频及同一会话多次制作的验收。真实任务正文、素材地址、截图和媒体只保留在本机。

- 本机轨迹记录两次 `interaction.ask`、对应用户反馈、`text_to_video` 的完整提示词和参数，以及按 `tool_call_id` 配对的工具返回。
- 本机运行日志补充两次 `Read`：视频技能说明与确认说明。日志仅披露文件名时，界面不会补造完整路径或读取正文。
- 视频工具报告 5 秒、1280×720、MP4；本机 `ffprobe` 核验为 5.056009 秒、1280×720、HEVC、24 fps、1 条音轨。
- 取得生成结果与完成交付分别表示；没有对应交付证据时，只标记“已生成”。
- 图生视频采到 `Read → FileBatchUpload → interaction.ask → image_to_video`，保留完整图片引用、提示词、时长、比例、模型及返回。下载的输入图片与本机原图 SHA256 相同，输出视频独立核验为 5.056009 秒、834×1112。
- 同一豆包会话可包含多次制作，每次单独保留需求、状态和产物，使用原会话 ID 和真实媒体引用建立关联。

## 视频相关能力

解析器按实际调用识别 `text_to_video`、`image_gen`、`image_edit`、`image_to_video`、`media_to_video`、`video_edit`、`present_files` 等工具。参数与返回始终保留，素材关系仅依据记录的资源 ID、URL 或本地路径建立。

当前文生视频和图生视频已在 Mac 本机实测。其他工具名称和参数来自本机豆包技能说明，支持解析不代表当前账号、版本及生成服务均已实测。图片生成、素材成片与视频修改需继续实际验收。

生成式修改与裁剪、拼接、字幕等普通剪辑属于不同流程。遇到未知工具，保留真实工具名与原始证据，不伪装成已经支持的步骤。

## 运行

源码需要 Python 3.12+ 与 PySide6 Essentials。

```sh
python desktop_main.py
```

Mac 源码启动器为 `launch-mac.command`，Windows 源码启动器为 `launch-windows.bat`。Windows 的豆包数据目录尚未实机验证。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Intel Mac 的本机安装版为 `/Applications/agentreions_doubao.app`，双击即可运行，无需额外安装 Python。开发打包另需 PyInstaller；运行 `python build_macos.py`，输出位于 `dist/`。`install_macos.py` 只安装本机构建，不会覆盖已有同名应用。

Windows x64 使用相同的 Qt 桌面界面和增量采集器，默认搜索 `%APPDATA%` / `%LOCALAPPDATA%` 下的豆包数据目录；找不到时可在界面选择“数据目录”。Windows 源码需 Python 3.12，构建运行 `python build_windows.py`，输出 `release/agentreions_doubao-Windows-x64.zip`，解压后启动 `agentreions_doubao.exe`。Windows 安装豆包的真实采集仍需实机验收。

Windows 会显式加载本机字体，并附带 [Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc) 作为中文后备；发行包保留其 [SIL OFL 许可](https://github.com/google/fonts/blob/main/ofl/notosanssc/OFL.txt)。本机微软字体只读取，不纳入发行包。包内截图验收同时检查字母和中文的字符覆盖。

GitHub 构建只上传源代码，CI 使用合成测试数据；不上传本机数据库、采集记录、素材或模型请求。发布到 AgentPair 仓库时，将工作流放在仓库根目录 `.github/workflows/agentreions-doubao-desktop.yml`，产品代码目录为 `agentreions_doubao/`。

视频独立核验需要本机 `ffprobe`，生成预览图还需要 `ffmpeg`。Windows 发行包不附带这两个工具，未安装时仍显示豆包的真实工具返回，并标记尚未独立核验。

Mac 的默认读取目录：

```text
~/Library/Application Support/Doubao/
```

应用自己的默认数据库：

```text
~/Library/Application Support/agentreions_doubao/observations.sqlite3
```

支持 `--db` 和 `--source-root`，用于隔离测试。原豆包目录始终只读。

## 数据与界面

- 增量字节游标、完整行提交、文件轮转与截断识别；新记录优先读取。
- Windows 小文件与任务说明核对完整内容；大文件每 30 秒启动一次分块完整性检查，按读取预算续扫。重启立即核对已保存的内容，内部改写不会只靠首尾抽样判断。
- SQLite WAL、小事务、单写入协调器；未变化的记录跳过，界面仅在数据变化时更新。
- 首屏直接展示生成工具详情；执行过程页支持播放、下一步、速度调整及点击查看。
- 提示词、时长、比例、模型、输入素材与输出结果分别展示；完整参数和返回直接可读、可复制。
- 独立接口页只显示具有明确调用 ID 或素材地址证据的 HTTP 记录；工具参数与 HTTP 正文分开。
- 工具报告与本机媒体核验分开。媒体下载仅由显式操作触发，采集本身不下载资源。
- 当前界面有界读取最近 20 个制作任务、每个任务 200 个步骤，超过范围会标识限制；不是后台数据容量上限。

## 覆盖范围

目前读取豆包保存在本机的会话轨迹、任务说明和部分运行日志，不代表完整模型输入、隐藏推理或云端生成器内部过程。HTTP 采集增量读取已存在的 SDK 网络日志；本次图生视频没有可明确关联的生成接口地址或 HTTP 请求／响应正文，界面不会用提示词或 CDN 地址填充。当前产品未接入全机进程、网络或文件监控。

## 验证

```sh
python -m unittest discover -s tests -v
```

真实数据和媒体保存在本机 `local-data/`；QA 截图、打包输出、数据库与原始采集内容不纳入代码发布。
