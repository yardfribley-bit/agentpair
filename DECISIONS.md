# Navigator 结构化决策

已集成官方开源 [System One Adapter](https://github.com/typesafe-ai/system-one-adapter-python)，固定依赖 `system-one-adapter[openai]==0.2.1`。现有模型中转上的 `deepseek-v4-flash` 负责结构化决策；不是 Jev 模型，不需要 TypeSafe Key。

在 Navigator 的服务器虚拟环境安装 `requirements-decisions.txt`，在私有配置增加 `jev: {"provider":"system_one_adapter","model":"deepseek-v4-flash","threshold":0.8}`。自动复用该服务器的 `relayToken`，不向 Driver 传递额外凭据。规划阶段检查外部调研需求和云端 Driver 必要性；调研判断仅记录，目前未接入通用搜索工具。模型不能单独授权创建机器，仍须原计划选择云端执行并通过资源限额。

验收阶段对目标、证据、结果一致性和最终答案进行独立评估，随后执行既有确定性规则。概率至少 0.8 才视为 yes，最多 0.2 为 no，中间为 unknown；这是尚未校准的运行阈值。服务失败或返回无效数据不能放行任务。完整适配器 debug 不进入任务记录。决策调用用量单独保存在 `answer.jev.usage`，尚未计入原工作台人民币费用估算。

每次复核分别判断目标完成、依据充分和结果一致性，返回 yes/no/unknown 及依据。程序根据这些字段选择 deliver、recheck 或 needs_information，覆盖模型笼统的通过声明。最多一次返工沿用任务引擎限制。

天气任务额外由程序检查数值、单位、原始时间的新鲜度和来源是否存在。地点匹配与自然语言答复的一致性当前仍依赖模型判断，来源存在也不等于独立验证可靠性。单源天气不声称多源核验。

未校准的模型自信程度不输出为准确率，confidence 保持 null。缺失验收字段不可通过。后续应通过错误放行率、误拒绝率和任务成功率评估独立决策是否改善结果。
