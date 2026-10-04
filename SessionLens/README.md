# SessionLens

独立于 AppLens 的 Codex 会话采集器。第一版采集用户消息、助手消息、源日志提供的 reasoning、工具调用与返回、用量和未知记录。完整请求上下文仍由 AppLens 负责。

## 当前实现

- 增量 JSONL 读取、尾行等待、重启游标、截断/文件替换代次。
- 事件及证据来源本地持久化；未知类型保留，坏记录显式标记。
- 上传确认与读取游标分离，按目标地址及令牌身份隔离确认记录。
- 平台独立 SessionLens 接口、设备令牌认证、按所有者读取会话。
- 平台处理层按 callId 关联工具往返，保留未知/不匹配计数。
- `/api/sessionlens/analyze` 将有明确覆盖标记的证据摘录交给已有 TaskEngine；分析异步运行，不阻塞采集。当前每次摘录受已有任务长度限制，不代表全会话分析。

## 启动采集

Python 标准库，无第三方依赖。在本目录运行：

```sh
python3 -m sessionlens --source ~/.codex/sessions --state runtime/collector.db --once
```

上传时设置 SESSIONLENS_TOKEN 为独立登记的设备令牌，并增加：

```sh
--endpoint https://你的平台域名/api/sessionlens/events
```

不加 `--once` 持续读取。默认每 3 秒检查限定目录；这不是模型输出流拦截，时效取决于源日志落盘。单轮每文件最多 1000 行、最多上传 20 批，单记录超过 16 MiB 阻止游标推进并报告错误；单事件超过 2 MiB 上传配额保留本地。

## 验证环境

本机验证接收器复用平台的 SessionStore，绑定 127.0.0.1:18950，使用 runtime/local-token 中的本地令牌。原文存在 runtime/collector.db，接收后存在 runtime/engine.db。运行目录不提交 Git。

这是本机 HTTP 入库与处理验证，不等于已经部署到 www.chuhaijian.com。平台源代码已经添加接口，远端部署尚未完成；真实模型分析采用服务器现有 AgentPair 中转 DeepSeek，结果保存在本机报告目录。

## 当前边界

已有本机会话报告前端，尚无桌面应用和自动启动。原文采集未自动脱敏，模型分析使用脱敏且有长度限制的证据片段。原文仅在受令牌保护的本机验证环境传输；正式云端启用前需补齐脱敏及可见上传范围。不能恢复源日志未提供或被截断的内容，不能从会话声明推断真实进程/网络行为。

重复的用户/助手记录保留来源版本，暂未合并；已验证单进程运行，尚无多实例锁与上传账号生命周期管理。不把该版本称为生产可部署版本。

## 检查

```sh
python3 -m unittest discover -s tests -v
```

平台 HTTP 测试：在 AgentPair 目录运行 `python3 -m unittest test_sessionlens_api -v`。

## 初步会话分析与展示

`sessionlens.analyze` 复用 AgentPair TaskEngine 的规划、分析、复核三阶段；模型请求通过服务器已有中转，模型为 deepseek-v3.2，服务器令牌留在服务器。SSH 认证需要环境变量 SESSIONLENS_SSH_PASSWORD，勿写入配置或提交。

```sh
python3 -m sessionlens.analyze --store runtime/engine.db --session 会话ID --output runtime/reports/会话ID
python3 -m sessionlens.report_site
```

展示地址 http://127.0.0.1:18951/ 。只绑定本机，展示实际模型输出，不提供原始数据库和报告 JSON 的下载。每份报告包含结论、用户需求、实际交付、关键发现及处理办法、执行故事、来源证据。引用可展开证据，保留事件 ID、源字节范围与截取标记。

有工具记录的会话当前取最后一轮已结束任务；仅消息的来源分析已采集消息并明确缺少工具执行证据。局部分析不代表完整会话审计，模型复核也不等于独立验证。
