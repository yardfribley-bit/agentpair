# 三产品统一界面实现与验收

基准：[批准的设计规范](../../design/unified-product-ui/design-spec.md)、[原型](../../design/unified-product-ui/unified-product-ui.html)、[共享 token](../../design/unified-product-ui/tokens.json)。

## 产品交付形态

| 产品 | 技术载体 | 交付内容 |
| --- | --- | --- |
| AppLens macOS | NSWindow + 本地 WKWebView | 请求列表、选中上下文、分类摘要、可收起和拖动的证据窗格、本机保存及平台回执 |
| AppLens Windows | WinForms + 本地 WebBrowser / IE11 | 同一桌面内容，原生窗口控制与本机动作桥；沿用真实采集和上报 |
| SessionLens macOS / Windows | Qt / PySide6 QMainWindow | 164 px 侧栏，顶部提问、答案与单步过程、历史任务、采集同步、独立证据窗格和固定本机状态栏 |
| AgentPair | Web 平台 | 196 px 侧栏、共享网页外壳；任务查询、设备、采集数据、安全审计、会话洞察、云机器及软件包 |

终端产品没有网站账号导航或产品/系统切换器。原生标题栏由系统绘制；AppLens 和 SessionLens 保持独立的采集状态。关闭应用的行为沿用现有实现，不宣称后台托盘能力。

所有产品使用白色内容表面、`#F7F8FA` 背景、`#2463EB` 主色以及明确文字状态。现有路由、权限、采集协议与用户模型目的地保留。

## 数据与交互修复

- 任务中心旧 CSS 的 `#deliverables{display:none!important}` 会隐藏已经完成的回答，已清理。输入区移到页面上方；当前问题和当前答案先展示，历史与处理过程默认折叠。平台查询不再展示代码任务专属的交付脚注。
- 设备搜索同步选中资产、设备详情、采集器与证据链接；空结果清理旧内容。单台设备可以同时展示 AppLens 和 SessionLens。
- AppLens 的历史正文与选中请求 ID、SHA256 一致后才显示。分类脚本由两端共享；本地 WebKit 跨页面访问改为共享脚本和消息通信。
- Windows 重新配对后以连接文件的当前设备身份为准，上下文同时核对平台地址和设备 ID，防止旧身份覆盖。正文缓存采用临时文件替换，并在损坏时恢复；仅保留当前列表的 100 份缓存，源记录不由此删除。
- SessionLens 查询期间从历史、项目或建议入口发起另一问题，会先提示等待，避免新问题与正在返回的旧答案错位。
- 安全审计默认显示发现与资产，证据和处置进入网页抽屉。关闭/Escape/更换设备会收起详情并停止回放。会话洞察先显示交付结论，再回放实际记录的一段过程。

## 本机验收

本轮验收使用隔离的虚构设备、会话与凭据，没有触发模型、云机器或真实客户端上报。

| 验收 | 结果 |
| --- | --- |
| 平台 HTTP / 采集器接入 / 权限 / 联合查询 / 静态资源 | 67 项通过 |
| SessionLens 完整测试 | 最终 194 项通过，包含忙状态导航修复；Mac Intel、Apple Silicon、Windows 流水线通过 |
| SessionLens Intel macOS 安装包 | PyInstaller 构建通过，打包后中英文 self-test 通过 |
| AppLens Intel macOS | 原生构建、库存 self-test、WKWebView 界面自测通过 |
| AppLens 桌面 DOM | 19 项通过，覆盖请求切换、错哈希、回执、原文、连接草稿与用户触发回放 |
| AgentPair 浏览器实际点击 | 本机真实 HTTP handler；核对当前问题与答案、追问、两类采集器、设备筛选与空结果、原文跳转 |
| Web DOM 回归 | 当前答案、回复反馈、跨会话状态、设备详情关联、16 项采集助手交互、安全证据抽屉通过 |
| 会话洞察生成 | 3 项通过，包含交付先于步骤、仅一段展开、证据转义与路径保留 |
| 独立代码复查 | 查出的忙状态输入错位、Windows 身份覆盖、缓存恢复缺口均已修正并复查 |

AppLens 界面自测调用真实 WKWebView 并输出截图；SessionLens 截图由真实 Qt 组件在 offscreen 模式输出。两者均使用 fixture，截图不表示已验证真实 WorkBuddy 全量采集。

## 跨平台流水线

- `sessionlens-desktop.yml`：Mac Intel、Apple Silicon 和 Windows，完整测试、打包及打包后自测。
- `applens-desktop-macos.yml`：Intel 和 Apple Silicon，原生构建、本地界面和原文/回执验收。
- `applens-context-windows.yml`：C# 编译、PowerShell 采集验证、安装包、安装后的正文/回执与桌面历史选择验收。
- `unified-product-ui.yml`：网页资源与 HTTP 入口、当前答案、设备关联、证据抽屉和洞察生成。

本机为 Intel Mac。Windows 和 Apple Silicon 由各自 GitHub Actions 运行器完成构建、自测，不能表述为在用户的 Windows / Apple Silicon 机器上实测。

最终源码修订：`fb489b57bc0c877fee3ac1919e6c333cedc2482f`。以下流水线均成功：

- [统一网页界面验收](https://github.com/yardfribley-bit/agentpair/actions/runs/37619226311)
- [AppLens macOS 双架构](https://github.com/yardfribley-bit/agentpair/actions/runs/37619226170)
- [SessionLens 三平台](https://github.com/yardfribley-bit/agentpair/actions/runs/37619226193)
- [AppLens Windows 安装后原生界面自测](https://github.com/yardfribley-bit/agentpair/actions/runs/37619226211)
- [Windows 设备安装程序](https://github.com/yardfribley-bit/agentpair/actions/runs/37619226210)
- [云机器流程回归](https://github.com/yardfribley-bit/agentpair/actions/runs/37619226216)

Windows 兼容性验收同时修复了测试读写默认编码的问题，明确 UTF-8；AppLens 回放 iframe 改为用户启动后加载，原生浏览器自测按页面就绪状态等待，保留正文、回执、哈希和设备身份的严格校验。

## 2026-10-07 线上部署

新版网页已部署到既有生产服务器。北京时间 20:40 发布修订 `fb489b5`，正式入口为 `https://www.chuhaijian.com/`。

- 白名单更新 25 个 Web 运行文件，包括共享资源、7 个模块页面及控制器、平台静态路由和洞察生成器。
- 会话洞察使用服务器现有的 4 对 `analysis.json` / `evidence.json` 生成 6 个 HTML 页面，没有上传本机 fixture 或重新调用模型。
- 保留账号、采集数据库、模型配置、证书、Nginx 和系统服务配置；发布前后核对配置及分析源文件哈希。
- 发布时以 SQLite 写事务暂缓新任务入队，核对没有活动任务后重启服务并立即释放；没有修改数据库记录。
- 回滚目录：私有回滚备份（路径保留本机），包含既有代码、洞察 HTML、哈希清单和 `rollback.py`。
- HTTPS 页面及共享 CSS / JavaScript 返回 200，资源 MIME 正常；服务为 active。公开站点目录没有原始 JSON。
- 实际数据 API 验收：9 台设备；所选设备的 AppLens、SessionLens 摘要分别成功读取 200 条记录（这是查询窗口，不是全库总量）；安全审计有 15 条已有发现。上线后的服务日志未出现 Python traceback。
- 本轮部署只更新网页；终端客户端安装状态不由此改变。

线上浏览器点击验收尚未完成：内置浏览器对线上标签页读取及刷新持续超时；TinyFish 自动化因本次页面内容授权不明确被自动审批拒绝，已向用户申请本次授权。在授权和点击结果到达前，不能把接口检查写成线上交互验收通过。

## 可复现的本机预览

```sh
python3 ops/unified_ui_preview.py --port 18956
```

仅监听 `127.0.0.1`，使用临时数据库和虚构设备。控制台显示 fixture 登录信息；退出后清理临时数据。问答使用固定的验收后端，仅验证页面与平台真实存储之间的连接，不作为自由问答准确率测试。
