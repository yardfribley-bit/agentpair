# TinyFish 与 taste-skill 遗漏核查

核查时间：2026-10-08，北京时间。只读核对本机源日志、SessionLens collector.db、生产搜索索引及原始接收库。未修改采集进度、上传队列或生产代码。

## 已确认的操作

- 2026-10-06 17:57:03，Codex 通过 exec 内的 exec_command 执行 sed，读取 product-references/taste-skill/SKILL.md；对应工具返回包含 Skill 说明。
- 2026-10-07 13:34:53，Codex 通过 exec 调用 tools.mcp__codex_apps__tinyfish_run_web_automation，针对 AgentPair 设备页做已授权的页面验收；对应工具返回已保留。

## 三个不同环节的缺口

1. 采集：本机同一源日志的持久化 cursor offset 为 186184781；该 TinyFish 调用从 byte offset 190063493 开始，尚未进入本机 events 表。源日志当时已有 285016293 字节。这是进度核查时的状态，不能据此推断永久丢失。
2. 上报：核对的两次 taste-skill 读取已存在本机 events，但 deliveries 表对应成功记录均为 0；生产 session_events 对这三条 callId 均未命中。生产数据中心仅检出 5 条提及 TinyFish 的用户消息，未检出 taste-skill。
3. 能力识别：data_center.py 只识别直接 MCP identity/namespace，未展开 exec 的 JavaScript 内嵌调用。Skill 路径识别只接受 installed skills 布局，product-references/taste-skill/SKILL.md 不符合。即使源记录抵达，当前结构化 MCP/Skill 搜索和排行仍会漏出。

## 身份与计数边界

TinyFish 的实际原始名称为 mcp__codex_apps__tinyfish_run_web_automation，原始 namespace 是 codex_apps，函数名是 tinyfish_run_web_automation。产品若按服务 TinyFish 展示，必须保留原始身份与服务别名的映射，不把不同 codex_apps 服务合成一个产品。

exec 中出现工具名称或调用表达式不等于实际执行成功。后续展开需保留外层 callId、嵌套调用位置、参数与返回关联，区分实际返回、发起调用、审批拒绝、示例代码和条件分支；轮询与启动浏览器任务也应按方法区分。

taste-skill 当前证据是说明读取。不能仅根据用户提到名称或下载文件，计为明确 Skill 工具加载或确认已遵循全部设计指令。

## 修复顺序

先定位实时源日志进度及本机上传积压，再补通用包装调用展开与 Skill 引用来源解析，最后以这两条真实历史操作验收搜索和排行。不能仅增加 tinyfish 或 taste-skill 的名称白名单。
