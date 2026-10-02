# WorkBuddy 采集边界与接入

现有 `~/.workbuddy/settings.json` 中的 LoongSuite 钩子必须保留。
AppLens 采集器为 `workbuddy_hook.py`，由 Python 3 执行，JSON 从 stdin 输入。
在 SessionStart、UserPromptSubmit、PreToolUse、PostToolUse、Stop 的现有
hooks 数组内追加 command 类型条目，不能覆盖其它 hook。
命令格式：`/usr/bin/python3 "<绝对路径>/workbuddy_hook.py" PreToolUse`。

采集器记录会话哈希、调用 ID、事件时间、工具名称和脱敏输入/输出。
本地存储位于 `~/Library/Application Support/AppLens/telemetry/hooks`。
钩子内容目前只保存在本机，不会通过元数据 OTLP 接口上传。
字段名敏感项及常见凭证格式会脱敏，但无法保证识别全部敏感文本。

限制：PreToolUse 是工具执行前，而非 LLM 请求加密前。
UserPromptSubmit 仅覆盖用户输入，不等于模型完整系统提示和上下文。
完整请求、响应、首 token 延迟和实际服务目标仍需单独确认模型调用层接口；
没有证据时不得用工具时长作为 LLM 延迟。
