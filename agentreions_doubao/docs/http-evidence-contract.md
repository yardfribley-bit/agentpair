# HTTP 证据接入契约

## 2026-10-09 本机实测

图生视频已真实执行，轨迹给出 `image_to_video` 的 `duration`、`image_reference_url_list`、`model_version`、`prompt`、`ratio` 和视频工具返回。这些属于工具协议，不是已经取得的 HTTP 请求正文。真实会话 ID、调用 ID、任务正文和素材只保存在本机验收记录中。

本机 `saman_netlog_2026.1008.0.log` 有 41 条抽样 `Request succeeded/failed/lagged` 记录，包括 HTTP 和 WebSocket；截至 10 月 9 日 00:03:47，而本次图生视频发生在 01:20 后。HTTP 条目实际可取 URL、响应状态码、网络错误码、请求／响应字节数、部分响应头、耗时和本地时间。日志没有记录 method、请求／响应正文、当前 session/call ID。

对上述网络日志、最新 `saman_2026.1009.0.log`、`netmain-0-2026-10-8.alaudalog` 只读扫描，得到 9 条可识别 HTTP 记录，**0 条与本次图生视频明确关联**。`AalG/Atab` 二进制网络日志不能假定为 JSON 或未加密 protobuf；当前没有已验证解码器。生成接口地址和 HTTP payload 仍未取得。

## 模块入口

`http_evidence.read_http_evidence(paths, session_id, call_ids=(), artifact_urls=(), max_bytes=...)` 只读取现有文件，不请求外网、不更改豆包设置。不要在每次 UI 刷新全量调用；需要时由采集工作线程运行，或将 `parse_http_line` 挂到已有增量游标。

返回：

```text
records[]                    明确关联当前会话／调用的 HTTP 证据
files[]                      文件格式、大小；不支持二进制会明确说明
errors[]                     文件不可读、行／扫描上限；不静默补全
parsedHttpRecords            本次扫描可识别的 HTTP 数量
unassociatedHttpRecords      无法明确归属当前任务的 HTTP 数量
coverage.completeHttpCapture false
```

单条 `records[]`：

```text
method                       实际方法或 null；不得默认 POST
url / host / path            日志实际 URL，登录凭据字段不进入展示
queryParameters[]            URL 中实际记录的参数
time / timeSource            记录的本地钟时间；无时区的源数据明确标记
statusCode / networkErrorCode
requestHeaders / responseHeaders
requestBody / responseBody   {status, value, truncated?, originalChars?}
requestBodyBytes / responseBodyBytes / elapsedMs
association                  explicit_call_id / explicit_session_id / exact_artifact_url
purpose                      unknown_http / artifact_transfer
source                       {path, line, sha256}
```

正文 `status` 为 `not_recorded`、`captured_json`、`captured_text`、`opaque_protobuf`。不可用字段保持为空。`application/x-protobuf` 正文即使捕获也不假装已解析；需要匹配版本的实际 schema。

UI 显示“HTTP 接口证据”时，可以独立展示方法、接口、URL 参数、请求正文、状态、实际返回、耗时与证据来源。字段缺失使用“日志未记录”；`records` 为空使用“该工具暂无可明确关联的 HTTP 记录”。不能把工具 `arguments` 放入 HTTP 正文栏，也不能把媒体 CDN 地址命名为生成接口。`artifact_transfer` 应显示“素材传输”，它证明资源请求，不证明生成服务调用。

关联仅接受明确 session/call ID，或与本次已知产物 URL 完全相等的 URL；时间相近、域名相同、文件名相似不能建确定关系。

## 如何补全下一层

1. 优先让豆包已公开或明确提供的本机调试接口、开发者工具网络事件或 HTTP 导出提供 request ID、method、URL、发送正文、响应正文。当前尚未证实桌面应用提供这种可附加接口，不能宣称已有完整采集。
2. 若以后取得 Chromium CDP 接入，使用 `Network.requestWillBeSent`／`Network.responseReceived`／`Network.getResponseBody` 等事件，在请求 ID 下关联；请求正文仍要依据真实捕获数据，并处理分块、SSE、protobuf。CDP 并不天然覆盖 native TTNet 的请求，须分别验收 Mac 和 Windows。
3. 没有应用调试接口时，选配显式 HTTP 代理才可能取 HTTPS 正文。这涉及用户配置与证书、兼容性和 native 网络栈覆盖；本次没有启用或修改任何代理／证书。只读现有日志的版本不能承诺取得这一层。

当前不应通过虚构接口地址或复制提示词填空来制造“完整 HTTP”。
