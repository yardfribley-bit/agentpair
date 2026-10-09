# AgentPair · VideoGen 工具调用宣传片

场景：在 AgentPair 数据中心搜索 `tool="VideoGen"`，从工具调用追溯用户需求、Agent、设备、提示词、参数、返回与关联核验记录。

宣传定位：让 Agent 的每次行动，有据可查。

- 1920×1080，30 fps；MP4/H.264/AAC。
- 中文旁白和画面字幕；原创的低音量配乐。
- 源码默认使用合成示例与示意界面，展示搜索、需求、调用参数、返回和文件核验的阅读顺序。
- 动画编排不表示产品界面已改版；工具完成状态和文件满足需求分别呈现，不宣称自动风险识别或自动阻断。
- 原始产品截图、会话证据、记录标识和既有成片验证材料保留本机，不随源码提交。

## 输出

- `AgentPair-VideoGen-宣传片.mp4`：本机生成的成片。
- `AgentPair-VideoGen-封面.jpg`：封面。
- `AgentPair-VideoGen.srt`：通用旁白字幕；重新生成音频和成片时会更新时刻。
- `storyboard.json`：不含真实会话内容的分镜与旁白，可编辑。
- `storyboard-preview.jpg`：全部分镜预览。

## 复现

需要 Python/Pillow/NumPy、FFmpeg 和 macOS 已安装的 Tingting 语音。

```sh
python3 prepare_audio.py
python3 render_video.py --preview
python3 render_video.py
```

音频合成须允许系统语音读取本机语音资源。默认复现不读取本机原始证据，也不访问服务器或调用模型。

如需制作已授权的本机证据版本，可显式传入 `--evidence-dir /path/to/private-evidence`。该目录包含 `search-evidence.json`、`record-evidence-0.json`，以及可选的 `video-search.jpg`、`video-call-result.jpg`。未提供截图时使用示意界面。文件核验数值来自详情对象的可选 `presentation` 字段（`requestedSeconds`、`observedDuration`、`width`、`height`）；未提供时显示待核对。仅使用有对应证据的数值。

`fetch_evidence.py` 是单独运行的本机取证工具，会从已配置平台读取 API 并在控制台显示内容；它不参与默认渲染流程。证据文件与输出影片保留本机。
