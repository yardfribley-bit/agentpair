# Windows WorkBuddy 完整模型输入采集

AppLens 使用与 macOS 一致的请求体采集协议，不再依靠最多 100000 个
UTF-16 单元的 WorkBuddy generation 日志证明请求完整性。

## 使用

1. 安装 AppLens，使用“我的设备”生成的配对码连接平台。
2. 启动 WorkBuddy。点击“启用完整正文采集”，保存工作并确认重启。
3. AppLens 备份 WorkBuddy 自身的代理字段，启动仅监听本机的打包代理，
   配置 WorkBuddy 的 `http.proxy` 和进程级 CA；不改系统代理或信任库，
   不关闭 TLS 校验。
4. 在 WorkBuddy 发起模型请求。列表中的“HTTP 正文 · 长度一致”表示
   捕获的传输字节数与 Content-Length 相等；上传列另显示云端哈希回执。
5. 点击调用打开平台，查看原文及数据分类。日志副本仍标为日志，不冒充
   HTTP 请求；模型名称直接来自请求正文的 model 字段。

采集仅匹配 copilot.tencent.com 的 v1/v2/v3 chat/completions 请求正文，
不保存 Authorization、Cookie、响应正文或独立工具调用事件。
暂停会删除记录开关，代理保持转发避免中断 WorkBuddy；退出时恢复代理
配置并尝试正常重启 WorkBuddy，随后关闭代理。若 WorkBuddy 拒绝退出，
不会强杀应用，停录后保留转发并提示手动退出。

原配置记录位于 `%LOCALAPPDATA%\AgentPair\workbuddy-proxy-original.json`。
恢复时仅覆盖仍指向本采集代理的字段，不覆盖用户后来手动修改的代理。
无需管理员权限、Python 环境或额外下载安装运行时。

## 验证边界

GitHub Windows CI 编译 GUI、安装安装包、自检文件，实际启动打包的代理，
用验证 CA 的 TLS 连接发送大于 150 KB 的原文到隔离的本机测试服务，
核验完整原文、SHA256、传输长度、忽略请求头和暂停开关。
PowerShell 测试另核验完整正文转为平台协议、回执和坏哈希拒绝。

这些测试不等同于用户电脑上的真实 WorkBuddy 联调。仍需在实际 Windows
设备上核对模型请求、AppLens 调用、云端正文和回执四者一致。
平台当前每条正文上限为 1000000 UTF-8 字节，不能把未上传的超限请求
宣称为成功。Android 独立应用不能复用桌面进程代理配置。
