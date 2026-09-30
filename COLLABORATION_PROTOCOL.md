# AgentPair 协作消息 v1

AgentPair 不把 SSH 会话当作 Agent 协作语义。Navigator、Driver A/B 交换的是版本化消息：
`protocol`、`id`、`correlationId`、`taskId`、`round`、`phase`、`kind`、`from`、`to`、
`summary`、`evidenceRefs`、`createdAt`。消息构造和接收方校验在
`agentpair/collaboration.py`。应用层不向另一节点发送密钥或整段审计原文。

状态必须区分：

1. **已记录/待发送**：Navigator 把消息放入本轮执行上下文；这不是网络投递回执。
2. **处理中**：目标分支开始调用模型或工具；仍不能推断已完成。
3. **已处理**：目标分支返回结果，结果和原消息 ID 关联。
4. **失败/中断**：目标分支没有返回可验证结果，不能标成已处理。

当前运行时仍通过 SSH 启动一次性远程 worker；消息协议已经与传输解耦，但**尚未取消 SSH**。
下一步传输层应改成由 Driver 主动向 Navigator 的 HTTPS 消息队列领取任务并回传结果，
使用每节点短期凭证、租约、幂等消息 ID、确认/超时重试和持久化队列。只有 HTTPS 路径
完成真实云机验收后，才能移除 SSH worker 调用；不能把消息封装本身称为已换传输。

界面展示的只是可观察事件、工具回执和交付物，不展示或杜撰模型的内部思维链。
