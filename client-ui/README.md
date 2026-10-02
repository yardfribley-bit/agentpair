# AppLens 采集现场

Mac WKWebView 与 Windows WinForms 内嵌浏览器共用 `capture.html`。

界面由客户端真实状态驱动：应用装配上下文与 HTTP 请求正文分开显示，保留最多 1200 字符的本机正文预览；显示本地待上传数量、上传中、失败重试及平台回执。

本机采集内容清单从匹配记录 ID / 正文哈希的原文解析：用户消息、system/developer 规则与背景、历史 assistant 消息、工具返回、图片引用，以及请求参数与附带字段。仅独立 Markdown 标题段落才标为 MEMORY.md 等内容；单独出现文件名不证明文件被读取。user_query 标记用于展示原文标记的用户任务。每类直接展示正文片段、大小、来源依据和“已随请求上传 / 本地保存”状态，展开可查看全部段落。定时刷新保留展开状态，不凭空显示当前模型回答。未采集模型响应，不绘制响应数据。动画只在新记录或上传状态变化时触发一次，首次加载历史记录不播放，遵循系统减少动态效果设置。

Mac 在上传操作前后发送 captureEvent，原文回执 SHA256 一致才确认接收。Windows 在相同节点写入原子替换的 capture-event.json，由窗口读取；上传失败保留未确认状态，重试仍使用已有去重机制。Windows 事件必须匹配平台、设备，且两分钟内有效。

平台回执只表示 AgentPair 收到已采集字节，不证明模型服务收到或处理。预览只在本地客户端展示，不增加采集范围或新的外部发送目标。

## 构建和检查

- Mac：`sh macos/build.sh`，生成 `macos/build/AppLens.app` 和 `AppLens-macOS.zip`。
- Windows：`windows/build.ps1`；GitHub Actions `AppLens Context Windows` 编译、采集 fixture 和安装包验收。安装包包含共用页面。
- 页面交互：安装 Playwright 后运行 `node client-ui/test-capture.cjs`。可通过 PLAYWRIGHT_MODULE、CHROME_PATH 指定已有运行时。

Windows 内嵌页面使用系统 WebBrowser / IE11 文档模式，禁止跳转外部地址及浏览器右键菜单。无法加载页面时不影响采集上传和现有调用表。
