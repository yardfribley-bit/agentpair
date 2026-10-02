# Windows 设备接入（采集连接器预览版）

入口 `/devices` 沿用设备原型的信息结构：客户端下载、设备清单、真实应用/进程、右侧分析表单。
Windows 提供 `AgentPair-Windows-Setup-0.1.0.exe`（未签名测试安装包），包含 .NET Framework Windows 窗口与 PowerShell 5.1+ 采集连接器。macOS/Android 尚未接入。

## 操作

1. 管理员登录，下载并安装 Windows 安装包，点击「接入 Windows 设备」。
2. 打开 AgentPair Windows，填写平台 HTTPS 地址与页面显示的配对码，点击「连接并开始采集」。服务器必须具备受 Windows 信任的 HTTPS 证书。
3. 首次配对码十分钟内有效，仅可使用一次。设备凭证由 Windows DPAPI 绑定当前用户后存储在 `%LOCALAPPDATA%\AgentPair`；后续运行配对码留空。
4. 每 30 秒上传清单；页面每 10 秒刷新；90 秒没有上传则显示离线。关闭连接器停止采集。
5. 选择应用或进程、填写目标，确认选中信息的分享范围后创建分析任务。不新开云机器。
6. 「解除设备绑定」撤销凭证并清除当前快照；已发布任务单独保留。

## 当前证据边界

- 进程：名称、PID、父 PID。无命令行、环境变量、文件内容。
- 应用：卸载注册表中的名称、版本、发布者；本机安装目录匹配到的进程名称。路径不上传。
- 应用目录匹配不完整：Store、便携应用、访问受限的进程可能缺失，不将“未匹配”当作“未运行”。
- 目前无网络采样/持续进程事件，不能证明数据外传。现有 AgentReins ETW 不受修改。
- 设备清单只允许管理员读取。选中数据经显式确认进入现有访客可读任务和已配置模型服务。
- 设备不能接收或执行远程命令；这是清单采集与分析入口，而非通用远程控制。

## 尚待完成

可信 HTTPS 的 UCloud Windows 实机验收；合并现有 AgentReins Windows 客户端（目前是独立 AgentPair 连接器）；自动配对安装体验；按任务追加 ETW 证据；分析结果回传客户端。本地单元/浏览器测试不能替代实机验收。

## 安装包构建

GitHub Actions `Windows device installer` 在 Windows Server 2022 上调用 `windows/build.ps1`：使用系统 C# 编译器生成客户端、Inno Setup 打包、静默安装、安装目录自检及窗口启动检查。客户端无需安装 Node、Rust 或 Python。此安装包尚未购买代码签名证书。
