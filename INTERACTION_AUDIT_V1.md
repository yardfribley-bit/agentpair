> 后续更新：当前为公开全局审计，新增安全调查与 AgentPair 语义复核，详见 [SECURITY_INVESTIGATION_V1.md](SECURITY_INVESTIGATION_V1.md)。本文保留早期实现记录，其中私有访问及仅统计分析的描述已被后续版本替代。

# AppLens 交互审计第一版

入口：`/model-security`，也可从模型数据页进入。接口：`GET /api/devices/interaction-audit/{deviceId}?request={requestId}`，只允许设备所属账号访问；撤销设备不可访问。

## 已实现

- 按单份已采集输入快照，默认展开用户、系统、历史 assistant、工具结果和工具调用请求。
- 每条消息并列展示方向、内容概览、规则候选、脱敏证据及原文定位。对应原文页保留完整已采集内容。
- 本地扫描凭据、个人/环境信息、指令覆盖与凭据外传语句，显示规则条件、版本、命中次数及能力缺口。
- 显示实际采集层、接收目的地主机、截断状态、正文 SHA256；同一位置多条规则命中合并展示。
- 分析过程不调用外部模型，不修改采集原文，不模拟响应或执行结果。

## 当前边界

输入中的历史 assistant 消息不是本次响应，工具结果不是独立执行取证。当前模型响应、授权策略、独立工具执行尚未接入，因此越权执行、权限过宽、危险输出执行均标记未评估。提示注入仅做语句候选检测，尚无语义与因果判断。无会话关联证据的快照不强行拼接。

正文脱敏采用本地已知模式，不能保证所有秘密已识别。命中次数包含多条规则匹配同一位置；不等于独立泄露事件。最多返回 100 处候选定位，省略数明确显示。系统消息概览 500 字符，其他消息 1400 字符，未解析正文 2400 字符；原文页仍保留全文。

## 验证与发布

- `python3 -m unittest test_interaction_audit test_model_context test_devices test_llm_evidence test_platform`
- `node test_interaction_audit_ui.cjs`（本地 Playwright + Chrome；模拟数据验证 DOM 安全、脱敏、刷新、桌面与窄屏布局）
- `node test_model_data_ui.cjs`
- `python3 ops/deploy_interaction_audit.py`：线上源文件基线检查、活动任务保护、文件备份、页面/匿名接口检查；失败恢复源文件并重启。

测试截图：`design/interaction-audit-desktop.png`、`design/interaction-audit-mobile.png`，截图数据为测试样例。
