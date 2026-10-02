# AgentPair 浏览器整合状态

## 当前执行路线（2026-09-30）

复用 WebLens 的 `internal/lightpanda` 实现和原测试，提取到 `browserkit/`。
不是将 WebLens 产品或插件直接部署到 Driver，也不继续重写 CDP 客户端。
Python 的 `browserkit_client.py` 通过 stdio 调用 Go 执行器，注册 browser.open、
browser.text、browser.snapshot、browser.click；结果作为 T 编号证据实时回传。
Driver 运行在独立云机，有网络，不使用 Docker。网页内容按不可信数据处理。
请求 URL 限制为任务提供的域名，但不宣称子资源与跳转有网络级隔离。
小型文本产物自动回传；Navigator 复核后返回用户可读答案。
截图、登录、上传下载和并发页面未验收，不向用户宣称可用。

构建、来源、边界见 [BrowserKit](browserkit/README.md)。

## 以下为历史设计（已被替代，不作为部署步骤）

复用项目：https://github.com/yardfribley-bit/weblens

已实现 WebLens `/api/live/open|snapshot|scroll|interact|close` 客户端，
以及 Driver 观察—动作—结果循环。每次最多 8 次模型决策，累计返回 token 使用、
带 B 编号的工具记录；Navigator 再复核。失败动作不能伪装成功；结束时关闭会话。
只传页面文本和标题，不传原始 HTML、Cookie、截图或密钥扫描结果。

当前未完成：独立云机镜像部署、浏览器子资源/跳转/DNS 的网络隔离、真实浏览器端到端测试、
实时流式动作展示、输入/上传/登录、截图验收、与代码预览服务的衔接。
因此生产调度在申请付费云机之前拒绝 browser 执行配置。不是已经上线可用的通用助手。

## 独立 Driver 的运行前提

1. WebLens 与 Lightpanda 运行在该 Driver，WebLens API 只监听 127.0.0.1。
2. 浏览器使用独立网络空间，阻断云元数据、管理网与非授权内网；出网代理约束跳转、子资源与 DNS。
3. 管理员配置 `/etc/agentpair-browser.json`，不能由任务文本或模型生成：

```json
{
  "endpoint": "http://127.0.0.1:8081",
  "egressIsolated": true,
  "allowedOrigins": ["https://example.com"],
  "allowedSelectors": {"https://example.com/": ["#expand-details"]}
}
```

`egressIsolated` 是部署验收声明，不会自动创建网络隔离，未实施不得设为 true。
默认不允许点击；预批准选择器不能替代操作语义确认，不应授权支付/删除等选择器。

后续验收：在隔离 Driver 上用可控测试站检查打开、滚动、点击、失败修正、会话清理，
记录资源消耗，再开放生产调度；不能仅通过 mock 测试就启用。
