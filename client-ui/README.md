# AppLens 终端采集界面

AppLens 是安装在本机的桌面应用。macOS 使用原生 NSWindow + WKWebView，Windows 使用原生 WinForms + WebBrowser，系统提供标题栏和窗口行为。两端共用 `macos/collector.html` 桌面内容、`context.js` 分类器及 `capture.html` 采集回放，不启动本地 HTTP 网站。

界面遵循 `design/unified-product-ui/design-spec.md`：164 px 本机侧栏、请求列表/选中上下文主从布局、可收起且可拖动的证据窗格、底部本机保存与平台确认状态。分类摘要先可见，原文按需展开；暂停/恢复、配对、打开本机目录及在平台查看均经过本机消息桥。

界面由客户端真实状态驱动：应用装配上下文与 HTTP 请求正文分开显示，保留最多 1200 字符的本机正文预览；显示本地待上传数量、上传中、失败重试及平台回执。

本机采集内容清单从匹配记录 ID / 正文哈希的原文解析：用户消息、system/developer 规则与背景、历史 assistant 消息、工具返回、图片引用，以及请求参数与附带字段。仅独立 Markdown 标题段落才标为 MEMORY.md 等内容；单独出现文件名不证明文件被读取。user_query 标记用于展示原文标记的用户任务。每类直接展示正文片段、大小、来源依据和“已随请求上传 / 本地保存”状态，展开可查看全部段落。定时刷新保留展开状态，不凭空显示当前模型回答。未采集模型响应，不绘制响应数据。回放仅由用户启动，切换请求或页面停止，不因实时刷新自动播放。回放采用原始记录分类片段，不改变平台回执状态；遵循系统减少动态效果设置。

Mac 在上传操作前后发送 captureEvent，原文回执 SHA256 一致才确认接收。Windows 在相同节点写入原子替换的 capture-event.json，由窗口读取；上传失败保留未确认状态，重试仍使用已有去重机制。Windows 事件必须匹配平台、设备，且两分钟内有效。

平台回执只表示 AgentPair 收到已采集字节，不证明模型服务收到或处理。预览只在本地客户端展示，不增加采集范围或新的外部发送目标。

## 构建和检查

- Mac：`sh macos/build.sh`，生成 `macos/build/AppLens.app` 和 `AppLens-macOS.zip`。
- Windows：`windows/build.ps1`；GitHub Actions `AppLens Context Windows` 编译、采集 fixture 和安装包验收。安装包包含共用页面。
- 本地 DOM 交互：安装 linkedom 后运行 `node client-ui/test-desktop.cjs`。可用 LINKEDOM_MODULE 指定既有模块。覆盖历史选择、错哈希拒绝、分类/证据、失败/回执及 native action 合同；这不代替实际渲染验收。
- macOS 原生界面：`macos/build/AppLens.app/Contents/MacOS/AppLens --ui-self-test /tmp/applens-native.png`。只使用虚构记录，不读取凭据、启动采集或外联；验证 WKWebKit 的选择、分类、回执、错哈希并输出快照。需要图形会话。
- Windows 原生界面：安装后 `AgentPairWindows.exe --desktop-ui-self-test`，由 build.ps1 自动运行。
- 页面交互：安装 Playwright 后运行 `node client-ui/test-capture.cjs`。可通过 PLAYWRIGHT_MODULE、CHROME_PATH 指定已有运行时。

Windows 内嵌页面使用系统 WebBrowser / IE11 文档模式，禁止跳转外部地址及浏览器右键菜单。共享脚本使用 ES5 与 IE11 支持的 Flex 布局。页面只可导航到打包的本地页面，采集正文通过 textContent 展示。分类器直接加载在主页面中，回放 frame 仅使用 postMessage 通信，不依赖 file:// 页面之间的跨域 DOM 访问。关闭窗口停止采集，最小化保持运行，不声称已有后台托盘能力。

Windows 的 `contexts` 目录是最近 100 份可见请求的正文缓存，按请求 ID 与正文 SHA256 定位，原子写入并核验哈希后展示；已损坏的缓存会在下一轮从源正文重建；只清理该缓存自己的过期文件，不删除 WorkBuddy 源 traces 或 network 日志。选择历史请求不会继续显示最新请求正文。

当前设备身份以 connection-state 为准，上下文状态必须同时匹配平台地址和设备 ID。重新配对会清空旧选择，等待本次连接状态，不能将旧设备上下文当作新身份的数据。
