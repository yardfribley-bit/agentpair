# Android 真实模型请求采集：环境核验

2026-10-02。目标是采集 WorkBuddy / 豆包实际发送的请求正文，分析哪些数据离开设备；UI 文本、前台活动和加密连接元数据不替代真实正文。

## 已核验

- 无线 ADB 已连接。当前可用客户端为 Android SDK platform-tools 36.0.2，ADB server 端口 5038。安装的 37.0.0 客户端配对失败；备用客户端初次自动连接触发 TLS 错误，关闭自动连接后配对成功。
- HONOR ALI-AN00，Android 15 / API 35，ARM64。ADB shell uid 2000，ro.debuggable=0，ro.secure=1，PATH 未发现 su。
- WorkBuddy：com.tencent.workbuddy.app，2.5.0，versionCode 650，targetSdk 36。已登录。run-as 被拒绝（not debuggable）。含 libflutter.so / libapp.so。
- 豆包：com.larus.nova，15.2.0，versionCode 15020040，targetSdk 35。run-as 被拒绝（not debuggable）。含 libsscronet.so / libttboringssl.so。
- WorkBuddy 网络安全配置：system CA；user CA 只存在于 debug-overrides。当前正式包未启用 debuggable。
- 豆包 release 网络安全配置：只有 system CA。
- 手机现有 AppLens 0.1.0。未升级、未清除配对、未更改手机代理或信任库。

## 结论与下一步

不能将 UI 可见文字当作发给模型的请求。两款正式包不允许 run-as，普通 ADB 不能直接注入采集器。网络安全配置也没有为正式包开放 user CA；仅安装用户证书不能据此保证 HTTPS 解密。原生 Flutter / Cronet 的实际行为及证书固定仍需动态验证，尚未声称已抓到正文。

需要可 root 的测试设备，或可调试/经授权仪表化测试包，才能验证发送前序列化正文 / TLS 写入的实际捕获路径。未对当前主力手机执行 root、重签或替换应用。

采集证据应包含包名与进程关联、目标 host/path、时间、HTTP 方法、序列化请求正文、长度和 SHA256；只分类真实捕获的数据，不推断缺失的服务端组装上下文。手机到应用后端的请求不自动等于后端到最终模型的请求。账号凭证与请求头不进入模型数据展示。

旧 UI 观察原型已移除；没有发布观察原型 APK。现有 macOS / Windows 采集流程和后端协议保留。
