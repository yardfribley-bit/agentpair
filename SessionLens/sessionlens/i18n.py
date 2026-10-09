"""Small product-text catalog. Historical content is never machine translated."""
import re

_language = 'zh'

CATALOG = {
    '当前问题正在处理，完成后再查询其他任务。': 'Your current question is being processed. Query another task after it finishes.',
    '原始要求、补充条件与方案确认': 'Original requests, additional constraints and confirmations',
    '回复内容与交付说明': 'Agent responses and delivery notes',
    '日志中实际记录的 reasoning': 'Reasoning actually present in the logs',
    '工具名称、输入参数、网址与路径': 'Tool names, inputs, URLs and paths',
    '工具输出、文件与错误信息': 'Tool output, files and errors',
    '会话附带背景，不代表完整模型输入': 'Recorded session context, not proof of full model input',
    '任务开始、结束及已记录用量': 'Task start, completion and recorded usage',
    '保留原文，类型尚未确认': 'Original content retained; record type unconfirmed',
    '搜索历史任务': 'Search task history',
    '找回过去的要求、执行记录和交付依据。': 'Find past requests, recorded actions and delivery evidence.',
    '本机任务库暂不可用，请稍后重试。': 'Local task history is temporarily unavailable. Try again shortly.',
    '没有找到匹配任务。可以换个关键词，或等待历史整理。': 'No matching task was found. Try another keyword or wait for history indexing.',
    '选择任务后查看要求与记录。': 'Select a task to view its request and records.',
    '在智能助手回顾': 'Review in Assistant',
    '已记录的动作': 'Recorded actions',
    '只展示当前已整理记录；在智能助手中继续核对过程与原始依据。': 'Shows indexed records. Continue in Assistant to inspect the process and original evidence.',
    '本机记录先保存到任务库，取得平台回执后才标为已接收。': 'Records are saved locally first, and marked received after a platform receipt.',
    '暂停采集': 'Pause collection', '恢复采集': 'Resume collection', '已暂停采集': 'Collection paused',
    '正在采集': 'Collecting', '本地任务库 · 采集尚未启动': 'Local task history · Collection has not started',
    '设置': 'Settings', '已启用': 'Enabled', '未启用': 'Disabled',
    '尚未配置日志目录': 'No log directory configured',
    '采集到什么': 'What is collected',
    '提问与回复、已记录思路、工具参数与返回、会话背景分别保存。': 'Questions, replies, recorded reasoning, tool inputs and returns, and session context are saved separately.',
    '本机保存 → 历史整理 → 平台接收': 'Local storage → History indexing → Platform receipt',
    '本机保存': 'Saved locally', '打开本机记录': 'Open local records',
    '本机统计暂不可用，采集记录未被删除。': 'Local statistics are unavailable; collected records have not been deleted.',
    '历史知识尚未整理': 'History has not been indexed', '尚未取得平台回执': 'No platform receipt yet',
    '历史整理状态未知': 'History indexing status unavailable',
    '暂停影响新记录读取和上报；正在处理的批次会先完成，本机历史问答仍可使用。': 'Pausing stops new reads and uploads after the current batch. Local history questions remain available.',
    '读取异常：': 'Read error: ',
    '这次任务是怎么做的？': 'How was this task completed?',
    'SessionLens · 智能助手': 'SessionLens · Assistant',
    'SessionLens · 任务监督与历史追溯': 'SessionLens · Task history',
    '智能助手': 'Assistant', '历史任务': 'Task history', '＋ 新对话': '+ New chat',
    '最近问过': 'Recent questions', '管理项目归属': 'Manage projects',
    '采集与同步': 'Collection and sync', '查看采集进度': 'Collection status',
    '本机历史知识库\nCodex · WorkBuddy': 'Local task history\nCodex · WorkBuddy',
    '查询范围': 'Scope', '全部 Agent': 'All agents', '时间': 'Time',
    '全部历史': 'All history', '最近 7 天': 'Last 7 days', '最近 30 天': 'Last 30 days',
    '全部任务': 'All tasks', '提问': 'Ask', '查询': 'Ask', '查询中…': 'Searching…',
    '重新查询': 'Ask again', '收起': 'Collapse', '原始依据': 'Original evidence',
    '向历史任务提问': 'Ask about task history',
    '问项目、一次任务，或当时为什么这样做…': 'Ask about a project, a task, or why a change was made…',
    '查找本机任务记录 · Enter 提问，Shift + Enter 换行': 'Search local tasks · Enter to ask, Shift + Enter for a new line',
    '有哪些项目': 'Which projects?', '为什么这样改代码': 'Why was the code changed?',
    '视频怎么生成': 'How was the video made?', '做过哪些项目？': 'Which projects have been worked on?',
    '最近的代码修改任务，为什么这样改？': 'Why was the code changed in the recent task?',
    '视频生成任务是怎么完成的？': 'How was the video generation task completed?',
    '从你的任务记录中寻找答案': 'Find answers in your task history',
    '本地任务库 · 采集状态可查看': 'Local task history · Collection status available',
    '还没有选中任务': 'No task selected', '正在查找相关任务': 'Finding related tasks',
    '正在查找相关工作记录…': 'Finding related task records…',
    '回答已完成 · 点击引用核对依据': 'Answer ready · Check citations against the evidence',
    '本次未能完成回答，可补充信息后重试': 'Could not complete this answer. Add details and try again.',
    '返回的回答与本次问题不一致，请重新查询': 'The answer does not match this question. Please ask again.',
    '正在查找这次问题的相关记录': 'Finding records related to this question',
    '语言已切换，但未能保存偏好；下次启动可能恢复原语言。': 'Language changed, but the preference could not be saved. The original language may return next time.',
    '问题已保留在上方。点击“重新查询”，根据这次问题重新寻找任务和证据。': 'Your question is preserved above. Click “Ask again” to find matching tasks and evidence.',
    '问题过长，请缩短到 2000 字以内。': 'Please shorten the question to 2,000 characters.',
    '任务知识库正在准备…': 'Preparing task history…', '知识库正在准备': 'Preparing task history',
    '仅本地采集': 'Local collection only', '未配置上报': 'Upload not configured',
    '等待新数据': 'Waiting for new records', '平台已确认接收': 'Platform receipt confirmed',
    '等待可发送记录 · 历史整理继续': 'Waiting for eligible records; history indexing continues',
    '本批已接收 · 继续同步其余记录': 'Batch received; remaining records are still syncing',
    '最新待上报源时间': 'Newest pending source time',
    '最新优先 75% · 历史保底 25% · 空闲份额可互用': '75% latest priority · 25% history share · Unused capacity is shared',
    '本机记录待平台确认': 'Local records awaiting platform receipt',
    '退避重试': 'Deferred retries', '超大记录保留本机': 'Oversized records retained locally',
    '历史上报索引待整理': 'Historical upload metadata awaiting indexing',
    '上报地址必须使用 HTTPS': 'The upload endpoint must use HTTPS',
    '历史索引已更新': 'History index updated', '项目目录正在低速整理': 'Indexing project directories in the background',
    '项目目录已更新': 'Project directories updated', '正在后台加载本机语义模型…': 'Loading the local semantic model…',
    '本机模型初始化结束，准备整理项目背景': 'Local model ready; preparing project context',
    '正在整理项目背景': 'Indexing project context', '正在整理关键词索引': 'Indexing keywords',
    '知识索引达到大小预算，整理已暂停；原始采集继续': 'Knowledge index budget reached; indexing paused while raw collection continues',
    '向量整理达到预算，已暂停': 'Vector index budget reached; indexing paused',
    '语义检索未就绪': 'Semantic search is not ready',
    '历史整理中': 'Organizing history', '仅本地保存': 'Saved locally',
    '本地采集运行中': 'Local collection is running', '正在读取日志': 'Reading logs', '仅本地': 'Local only',
    '你': 'You', '推断：': 'Inferred: ', '记录未能确认：': 'Not confirmed by records: ',
    '本次参考的任务': 'Tasks referenced in this answer', '查看这次任务过程': 'View this task process',
    '大记录仅显示已索引摘录，完整原文仍保留在本机。': 'Large records show indexed excerpts; complete originals remain on this device.',
    '原始证据': 'Original evidence',
    '所属任务：': 'Task: ',
    '最多比较 3 个检索到的任务，受所选来源、时间和当前整理进度限制。': 'Compares up to three retrieved tasks, limited by the selected source, time range and current indexing progress.',
    '这条记录不在本次回答的引用摘录里；可在对应步骤查看原始参数与返回。': 'This record is not in the cited excerpts. Inspect original parameters and returns in the corresponding step.',
    '这条原始记录暂不可用；回答引用的摘录仍保存在本次对话中。': 'This original record is unavailable; its cited excerpt remains saved in this conversation.',
    '采集尚未启动': 'Collection has not started', '当前机器上的日志 → 本地任务库 → 平台接收': 'Device logs → Local task history → Platform',
    '已整理内容：用户提问、回复、已记录的思路、工具调用与结果': 'Indexed: user questions, replies, recorded reasoning, tool calls and results',
    '上报与采集设置': 'Upload and collection settings', '关闭': 'Close', '取消': 'Cancel',
    '保存关联': 'Save association', '修正任务关联': 'Correct task association',
    '修正项目关联': 'Correct project association', '项目名称': 'Project name',
    '项目目录': 'Project directory', '仓库地址': 'Repository URL', '归属': 'Association',
    '关联到项目': 'Link to a project', '设为独立任务': 'Set as an independent task',
    '恢复自动判断': 'Restore automatic association', '从这一轮开始作为独立任务': 'Start an independent task from this turn',
    '任务需要重新核对': 'Task needs review', '选择要查看的任务': 'Choose a task',
    '等待补充任务信息': 'More task details needed', '尚未确认对应任务': 'Task has not been identified',
    '确认项目': 'Confirm project', '根据问题识别项目': 'Identifying the project',
    '找到多个相近任务，请选择具体的一次': 'Choose one of the related tasks',
    '此前的回答没有对应到你问的任务': 'The earlier answer did not match the requested task',
    '你想了解哪一次任务？': 'Which task would you like to review?',
    '项目知识正在准备中，采集记录仍保留在本机。': 'Project history is being prepared. Collected records remain on this device.',
    '项目归属已变化，请从项目总览重新选择。': 'Project associations changed. Select a project again.',
    '还没有找到这个项目。请补充项目名称，或在项目总览确认名称与目录。': 'Project not found. Add its name, or confirm its name and directory in the project list.',
    '找到同名项目或多个 Agent 的开发记录，请选择具体项目。': 'Several matching projects or agents were found. Choose a project.',
    '这个项目有多项任务，请确认你指的是哪一项。': 'This project has several tasks. Please choose one.',
    '任务历史': 'Task history', '项目与任务': 'Projects and tasks', '项目总览': 'Projects',
    '从项目或需求继续了解': 'Explore a project or task',
    '可以查询项目、回顾一项任务，或核对当时的工具输入与返回。': 'Explore projects, review a task, or inspect recorded tool inputs and results.',
    '等待查询': 'Ready for a question', '需要选择': 'Selection needed', '需要补充信息': 'More details needed',
    '选择要回顾的项目': 'Choose a project to review', '选择要回顾的任务': 'Choose a task to review',
    '现有记录对应多个候选，请选择具体记录。': 'Several records match. Please choose one.',
    '这次查询未完成': 'This query was not completed', '查询失败': 'Query failed',
    '索引已完成': 'Index ready', '索引仍在准备': 'Index is being prepared',
    '归属待确认': 'Association unconfirmed', '用户已确认': 'Confirmed by the user',
    '已识别': 'Identified', '已排除': 'Excluded', '已关联记录': 'Linked records',
    '记录支持': 'Supported by records', '分析推断': 'Inferred', '尚未确认': 'Unconfirmed',
    '未知': 'Unknown', '未记录': 'Not recorded', '无法确认': 'Cannot confirm',
    '模型次数无法确认': 'Model call count unknown', '记录统计': 'Recorded counts',
    '用户想解决什么': 'What the user wanted', '如何考虑这次修改': 'Why this change was considered',
    '执行涉及哪些部分': 'Parts involved in execution', '原始用户要求': 'Original user request',
    '当时的考虑': 'Recorded reasoning', '需求与反馈': 'Requests and feedback',
    '修改文件': 'Changed files', '修改文件记录': 'Changed file records',
    '修改原因尚未确认': 'Reason for the change unconfirmed', '修改文件尚未确认': 'Changed files unconfirmed',
    '一次任务里的内容往返': 'Recorded flow within a task', '下一步': 'Next step',
    '播放过程': 'Play process', '暂停': 'Pause', '播放速度': 'Playback speed',
    '当前': 'Current', '正在回放': 'Playing', '回放完成': 'Playback complete',
    '这一轮的思路与后续内容关联': 'Reasoning and subsequent content for this step',
    '查看参数与原始返回位置': 'Parameters and original return locations',
    '打开原始参数与返回': 'Open original parameters and returns',
    '工具参数与原始返回': 'Tool parameters and original returns',
    '查看思路原文': 'View original reasoning', '查看这一轮思路原文': 'View reasoning for this step',
    '已记录思路': 'Recorded reasoning', '已读取的思路记录': 'Reasoning records read',
    '记录支持的考虑': 'Reasoning supported by records', '按顺序找到的候选思路': 'Candidate reasoning by record order',
    '原始消息链关联': 'Original message chain', '按记录顺序候选关联，待核对': 'Candidate association by record order; review needed',
    '查看任务标识与原始记录位置': 'Task identifiers and original record locations',
    '修正需求关联': 'Correct request association', '修正项目归属': 'Correct project association',
    '需求与确认历程': 'Requests and confirmations', '查看项目内容范围': 'View project scope',
    '返回了什么': 'What was returned', '用户发言': 'User turn', '参数': 'Parameter',
    '记录路径': 'Recorded paths', '最后做成了吗？查看交付与核验': 'Was it completed? View delivery and verification',
    '当前记录没有工具调用。': 'No tool calls are present in these records.',
    '没有可读的参数字段。': 'No readable parameter fields.',
    '没有找到对应的工具返回，结果尚未确认。': 'No linked tool return was found; the result is unconfirmed.',
    '这一调用没有找到可关联的思路记录。': 'No reasoning record was linked to this call.',
    '思路与工具调用的原始关系未记录。': 'The original reasoning-to-tool relation was not recorded.',
    '后续内容关联未知。': 'Subsequent content association is unknown.',
    '没有可确认的修改文件记录。': 'No confirmed changed-file records.',
    '未读取到可关联的用户发言。': 'No linked user turns were read.',
    '未读取到可关联的思路原文。': 'No linked original reasoning was read.',
    '当前记录未提供可关联的原始要求。': 'These records do not provide a linked original request.',
    '没有找到可支持这次修改原因的关联思路记录。': 'No linked reasoning record supports the reason for this change.',
    '没有找到可确认的修改路径；工具调用记录不等于修改已完成。': 'No confirmed changed path was found. A tool call does not establish that a change completed.',
    'SessionLens 会话日志没有完整的逐次模型请求记录，当前无法确认实际调用次数。': 'SessionLens logs do not contain a complete record of individual model requests; the actual call count cannot be confirmed.',
    '会话日志未完整记录模型请求，不能确认 Agent 调用大模型的总次数。': 'The logs do not fully record model requests; the total number of model calls cannot be confirmed.',
    '模型节点及连线仅表示日志消息关联，不能证明实际网络 API 请求次数。': 'Model nodes and links show recorded message associations; they do not establish the number of network API requests.',
    '采集与上报设置': 'Collection and upload settings', '采集状态 / 设置': 'Collection status / Settings',
    '上报接口': 'Upload endpoint', '设备令牌（仅本次运行）': 'Device token (this run only)',
    'AgentPair 分析接口': 'AgentPair analysis endpoint', '分析授权文件': 'Analysis credential file',
    '助手模型地址': 'Assistant model endpoint', '助手模型名称': 'Assistant model name',
    '模型密钥文件': 'Model credential file', '启用本机语义检索（不上传日志）': 'Enable local semantic search (no log upload)',
    '本地 embedding 模型目录': 'Local embedding model directory',
    '目录以分号分隔。开启上报后，会发送所选日志的完整记录。\n未配置接口和令牌时，仅保存在本机；关闭应用停止采集。': 'Separate directories with semicolons. Uploading sends complete records from the selected logs.\nWithout an endpoint and token, records stay local. Closing the app stops collection.',
    '监工模式': 'Live tasks', '历史追溯': 'Task history', '跟随最新动作': 'Follow latest action',
    '尚未开始采集': 'Collection has not started', '搜索任务、文件或问题…': 'Search tasks, files or questions…',
    '查找任务': 'Find tasks', '理解这次任务': 'Understand this task', '关联多轮对话': 'Link conversation turns',
    '当前任务': 'Current task', '相关任务': 'Related tasks', '对应证据': 'Linked evidence',
    '标记待核实': 'Mark for review', '取消待核实标记': 'Remove review mark',
    '查看来源定位与原始片段': 'View source locations and original excerpts',
    '导出完整记录': 'Export complete record', '采集原文与证据来源': 'Original records and evidence sources',
    '回顾已采集的 Agent 记录': 'Review collected agent records', '已采集项目': 'Collected projects',
    '项目记录': 'Project records', '已关联的用户需求': 'Linked user request',
    '记录中的源码路径': 'Source path in the records', '根目录文件': 'Root directory files',
    '回顾某项任务的执行过程': 'Review a task process', '核对用户发言和模型调用次数': 'Check user turns and model call counts',
    '核对某项任务的用户发言和模型调用次数': 'Check user turns and model call counts for a task',
    '当前证据未提供用户发言的细分统计。': 'These records do not break down user turns.',
    '模型 API 调用次数无法确认': 'Model API call count cannot be confirmed',
    '记录的工具调用': 'Recorded tool call', '日志中关联的工具返回': 'Tool return linked in the logs',
    '日志思路与工具调用关联': 'Recorded reasoning linked to the tool call',
    '源消息链关联后续模型记录': 'Source message chain linked to a subsequent model record',
    '已记录模型消息': 'Recorded model message', '模型身份未知': 'Model identity unknown',
    'Agent 来源未知': 'Agent source unknown', '工具未记录': 'Tool not recorded',
    '已记录关系': 'Recorded relation', '节点间的原始关系未记录': 'The original relation between nodes was not recorded',
    '返回状态': 'Returned status', '错误': 'Error', '消息': 'Message', '退出码': 'Exit code',
    '标准输出': 'Standard output', '错误输出': 'Standard error', '输出': 'Output',
    '内容': 'Content', '文件': 'Files', '产物': 'Artifacts', '结果': 'Results',
    '路径': 'Path', '本地路径': 'Local path', '文件路径': 'File path', '网址': 'URL',
    '生成提示词': 'Generation prompt', '分辨率': 'Resolution', '画面比例': 'Aspect ratio',
    '生成音频': 'Generate audio', '保存目录': 'Output directory', '输入图像': 'Input image',
    '结束画面': 'End frame', '交付文件': 'Delivered files', '搜索内容': 'Search query',
    '候选数量': 'Result limit', '执行命令': 'Command', '用途': 'Purpose',
    '修改前': 'Before', '修改后': 'After', '本机地址': 'Local URL', '外部网址': 'External URL',
    '纬度': 'Latitude', '经度': 'Longitude', '预报天数': 'Forecast days', '地点': 'Location',
    '时区': 'Time zone', '实况字段': 'Current weather fields', '每日预报字段': 'Daily forecast fields',
    '参数摘录（正文较长，可查看原文）': 'Parameter excerpt (open the original for full content)',
    '下载地址': 'Download URL', '数量': 'Count', '工具返回空对象': 'The tool returned an empty object',
    '空列表': 'Empty list', '未记录关联返回，无法确认执行结果。': 'No linked return was recorded; execution results cannot be confirmed.',
    '提出需求': 'Request', '确认': 'Confirmation', '开始执行': 'Execution instruction', '调整': 'Revision',
    '工具调用': 'Tool call', '工具返回': 'Tool return', '解题思路': 'Recorded reasoning',
    '按 callId 关联的返回': 'Return linked by callId', '返回关系未附图证据': 'Return relation has no graph evidence',
    '返回未记录': 'Return not recorded', '已记录模型思路': 'Recorded model reasoning',
    '后续模型记录': 'Subsequent model record',
    '后续模型记录（日志关联，不能证明实际网络 API 请求次数）': 'Subsequent model record (log association does not establish network API request counts)',
    '任务监督与历史追溯': 'Task monitoring and history',
    '选择任务后提问 · AgentPair · 仅发送当前任务证据': 'Select a task to ask · AgentPair · Uses only the selected task evidence',
    '只展示已记录的动作；待核实标记不会暂停 Agent。': 'Displays recorded actions only. Review marks do not pause the agent.',
    '问当前任务：当时为什么这样做？用了什么参数？结果可靠吗？': 'Ask about this task: why, which parameters, and what evidence supports the result?',
    'SessionLens · 项目总览': 'SessionLens · Projects', '查找项目名称或目录': 'Search project names or directories',
    '全部开发目录': 'All development directories', '自动识别项目': 'Automatically identified projects',
    '人工确认项目': 'User-confirmed projects', '待确认目录': 'Unconfirmed directories',
    '选择一个项目': 'Choose a project', '修改依据': 'Change evidence',
    '修改依据 · 最近 100 条': 'Change evidence · Latest 100 records',
    '相关任务 · 最近 100 个': 'Related tasks · Latest 100 tasks',
    '确认 / 更名 / 合并 / 排除': 'Confirm / Rename / Merge / Exclude',
    '修正项目清单': 'Correct project list', '显示名称': 'Display name', '归类': 'Classification',
    '保持独立': 'Keep separate', '合并到已有项目': 'Merge into an existing project', '保存': 'Save',
    '项目标记 + 源码记录': 'Project markers + Source records', '用户确认': 'User confirmation',
    '日志记录了目录内的项目清单写入': 'The logs record a project manifest written in the directory',
    '本机现存项目标记；不能倒推历史仓库': 'Current local project markers do not establish historical repositories',
    '仅有源码修改路径，项目身份待确认': 'Only source change paths are available; project identity is unconfirmed',
    '看 Agent 参与了哪些开发，追溯每个项目的要求与修改。': 'Explore agent development work and review each project’s requests and changes.',
    '没有符合条件的项目': 'No matching projects', '保留为待确认目录': 'Keep as an unconfirmed directory',
    '确认为开发项目': 'Confirm as a development project', '排除临时脚本 / 输出目录': 'Exclude temporary scripts / output directories',
    '恢复自动识别': 'Restore automatic identification', '原始证据暂不可用': 'Original evidence is temporarily unavailable',
    '当前已采历史整理完成；新记录持续归入。': 'Collected history is indexed; new records continue to be added.',
    '历史正在逐步整理，当前数量还会增加；新记录优先处理。': 'History is being indexed. Counts may increase; new records take priority.',
    '依据是已采集日志里的源码写入、修改请求；不等于项目已完成。仅查看、安装和文档任务不计入。Shell 内写文件目前不计入此清单。': 'Based on source write and edit requests in collected logs; this does not establish project completion. Read-only, installation, document and shell-only writes are excluded.',
    '你的原始要求': 'Your original request',
    '按记录顺序展示；较长任务分批展开，原文完整保留。': 'Shown in record order. Long tasks expand in batches; original records are retained.',
    '当前索引尚未找到匹配任务': 'No matching task in the current index',
    '历史仍在后台整理时，尚未索引的任务暂时无法搜索。这不代表原日志中没有记录。': 'While history is being indexed, unindexed tasks cannot be searched. This does not mean that the original logs lack records.',
    '选择任务后展示对应原文。': 'Select a task to view its original records.',
    '本轮中断': 'Turn interrupted', '还不能确定的地方': 'What remains uncertain',
    '这次还有什么没说明白': 'What still needs clarification', '日志证据': 'Log evidence',
    '会话': 'Session', '来源': 'Source', '字节': 'Bytes', '查看依据': 'View evidence',
    '点击一步核对原文。摘要是记录摘录，不代表独立验证的结论。': 'Select a step to inspect its source. Summaries are excerpts, not independently verified conclusions.',
    '完整原文保存在本机，可导出核对。': 'Complete original records are stored locally and can be exported for review.',
    '识别依据：': 'Identification basis:', '大记录仅展示索引摘录：': 'Large records show indexed excerpts only:',
    '当前片段未确认工具返回之后的模型消息关系。': 'This excerpt does not confirm the model message relation after the tool return.',
    '返回摘录不完整，完整记录见原文。': 'The return excerpt is incomplete; see the original record.',
    '未记录最近任务内容': 'Recent task content was not recorded',
    '现有记录未附任务内容或目录': 'Task content and directory were not attached to these records',
    '已采集日志中的 Write/Edit 源码写入与修改请求。识别依据包括目录内记录的项目清单、本机现存项目标记或人工确认；本机目录状态不认证历史仓库。相同名称副本待确认，不自动合并。不含仅浏览、安装、文档、Shell 内写文件或未采集设备；工具请求不代表成功交付。': 'Source write/edit requests in collected logs. Identification uses recorded manifests, current local markers or user confirmation; local directory state does not establish historical repositories. Same-name copies remain unconfirmed and separate. Browsing, installation, documents, shell-only writes and uncollected devices are excluded. Tool requests do not establish successful delivery.',
    '任务数来自已找到的源码修改关联，并按现有多轮需求归属去重；不是全量项目任务。目录组件按路径归类，不推断业务功能；未采集和仅 Shell 写文件尚未纳入。': 'Task counts come from linked source changes, deduplicated by request association; they do not cover every project task. Components are grouped by path. Uncollected changes and shell-only writes are excluded.',
}

_PATTERNS = [
    (r'找到 (\d+) 个已整理任务', lambda m: f'{m[1]} indexed tasks found'),
    (r'原始记录保存在本机：(.+)', lambda m: 'Original records are stored on this device: ' + m[1]),
    (r'知识库可检索 (\d+) 个任务', lambda m: f'{m[1]} searchable tasks'),
    (r'证据整理完成 (\d+) 个', lambda m: f'{m[1]} tasks with indexed evidence'),
    (r'待整理 (\d+) 个', lambda m: f'{m[1]} tasks pending indexing'),
    (r'本机语义任务 (\d+) 个', lambda m: f'{m[1]} locally embedded tasks'),
    (r'正在发送 (\d+) 份记录', lambda m: f'Sending {m[1]} records'),
    (r'历史整理 (\d+)/(\d+) · 新任务优先', lambda m: f'Indexing history {m[1]}/{m[2]} · New tasks take priority'),
    (r'正在准备 (codex|workbuddy) 近期任务 · 历史低速整理', lambda m: f'Preparing recent {m[1]} tasks · History indexed in the background'),
    (r'(上报失败|任务索引等待重试|项目目录等待重试|项目目录暂不可用|本机语义检索暂不可用|知识索引等待重试|本机知识整理等待重试|知识索引暂不可用)：(.+)', lambda m: {'上报失败':'Upload failed','任务索引等待重试':'Task index waiting to retry','项目目录等待重试':'Project index waiting to retry','项目目录暂不可用':'Project index unavailable','本机语义检索暂不可用':'Local semantic search unavailable','知识索引等待重试':'Knowledge index waiting to retry','本机知识整理等待重试':'Local knowledge indexing waiting to retry','知识索引暂不可用':'Knowledge index unavailable'}[m[1]] + ': ' + m[2]),
    (r'查看依据 · (\d+) 次已读取用户发言、思路与修改文件', lambda m: f'View evidence · {m[1]} recorded user turns, reasoning and changed files'),
    (r'查看 (\d+) 轮完整需求与确认历程', lambda m: f'View all {m[1]} request and confirmation turns'),
    (r'(当前|正在回放|回放完成)：第 (\d+) / (\d+) 步 · 仅展示已记录的关系', lambda m: f'{CATALOG[m[1]]}: step {m[2]} / {m[3]} · Recorded relations only'),
    (r'核对来源 ([A-Za-z0-9_-]+)', lambda m: 'Check source ' + m[1]),
    (r'(.+) · 输入', lambda m: m[1] + ' · Input'),
    (r'(\d+) (轮用户发言|轮方案确认|轮开始执行|次工具调用|条 Agent 回复记录|项已关联任务|个记录路径)', lambda m: m[1] + ' ' + {'轮用户发言':'user turns','轮方案确认':'confirmation turns','轮开始执行':'execution turns','次工具调用':'tool calls','条 Agent 回复记录':'agent reply records','项已关联任务':'linked tasks','个记录路径':'recorded paths'}[m[2]]),
    (r'模型 API 调用次数：(无法确认|\d+)', lambda m: 'Model API calls: ' + ('Cannot confirm' if m[1]=='无法确认' else m[1])),
    (r'查看全部 (\d+) 个文件记录', lambda m: f'View all {m[1]} file records'),
    (r'查看其余 (\d+) 个已读取项目与任务', lambda m: f'View {m[1]} more projects and tasks'),
    (r'查看其余 (\d+) 个需求任务', lambda m: f'View {m[1]} more tasks'),
    (r'过程展示选取了 (\d+) / (\d+) 条记录。', lambda m: f'The process view includes {m[1]} / {m[2]} records.'),
    (r'现有记录包含 (\d+) 个已识别项目、(\d+) 个用户确认项目，以及 (\d+) 个待确认目录。', lambda m: f'The records contain {m[1]} identified projects, {m[2]} user-confirmed projects and {m[3]} unconfirmed directories.'),
    (r'采集 (Codex|WorkBuddy)', lambda m: 'Collect ' + m[1]),
    (r'(\d+) 轮用户发言，模型 API 调用次数无法确认', lambda m: f'{m[1]} user turns; model API call count cannot be confirmed'),
    (r'其中 (\d+) 轮方案确认、(\d+) 轮开始执行，均计入用户发言。', lambda m: f'Includes {m[1]} confirmation turns and {m[2]} execution turns, counted as user turns.'),
    (r'最近任务时间：(.+)', lambda m: 'Latest task time: ' + CATALOG.get(m[1], m[1])),
    (r'最近记录：(.+)；未附最近任务内容', lambda m: 'Latest record: ' + m[1] + '; recent task content unavailable'),
    (r'最近需求：(.+?)。路径组件：(.+)', lambda m: 'Latest request: ' + m[1] + '. Path components: ' + m[2]),
    (r'最近需求：(.+)', lambda m: 'Latest request: ' + m[1]),
    (r'记录目录：(.+)', lambda m: 'Recorded directories: ' + m[1]),
    (r'(任务|会话|来源|项目)：(.+)', lambda m: {'任务':'Task','会话':'Session','来源':'Source','项目':'Project'}[m[1]] + ': ' + m[2]),
    (r'第 (\d+) 步，(.+)', lambda m: f'Step {m[1]}, ' + m[2]),
    (r'([\d.]+) (?:轮)?用户发言', lambda m: m[1] + ' user turns'),
    (r'后续记录中还有工具调用：(.+)。与本次返回的后续消息关系未确认。', lambda m: 'Later records include another tool call: ' + m[1] + '. Its message relation to this return is unconfirmed.'),
    (r'已识别 (\d+) 个项目　·　待确认 (\d+) 个目录　·　已排除 (\d+) 个', lambda m: f'{m[1]} identified projects · {m[2]} unconfirmed directories · {m[3]} excluded'),
    (r'(\d+) 个任务', lambda m: m[1] + ' tasks'),
    (r'(\d+) 个源码文件', lambda m: m[1] + ' source files'),
    (r'(\d+) 个关联任务', lambda m: m[1] + ' linked tasks'),
    (r'(\d+) 次写入/修改请求', lambda m: m[1] + ' write/edit requests'),
    (r'需求对话 · (\d+) 轮', lambda m: 'Request conversation · ' + m[1] + ' turns'),
    (r'基于 (\d+)\s*/\s*(\d+) 条任务记录；部分长记录可能截取。', lambda m: f'Based on {m[1]} / {m[2]} task records; long records may be truncated.'),
    (r'(\d+) 轮对话', lambda m: m[1] + ' conversation turns'),
    (r'继续展开 · 已显示 (\d+) / (\d+) 个步骤', lambda m: f'Expand more · Showing {m[1]} / {m[2]} steps'),
    (r'(\d+) 个工具调用尚未找到对应返回。', lambda m: f'{m[1]} tool calls have no linked return.'),
    (r'原始消息链关联后续记录：(.+)', lambda m: 'Subsequent record linked by the original message chain: ' + m[1]),
]


def _normalize(value):
    value = str(value or '').lower().replace('_', '-')
    if value in ('english', 'en') or value.startswith('en-'):
        return 'en'
    if value in ('中文', 'chinese', 'zh') or value.startswith('zh-'):
        return 'zh'
    return None


def set_language(value):
    global _language
    normalized = _normalize(value)
    if normalized is None:
        raise ValueError('Unsupported interface language')
    _language = normalized
    return _language


def language():
    return _language


def answer_language(question, config=None):
    """Explicit response preference wins; otherwise follow the question language."""
    explicit = _normalize((config or {}).get('responseLanguage'))
    if explicit:
        return explicit
    text = str(question or '')
    if re.match(r'\s*(?:how|what|which|why|where|when|list|show|tell|explain|summarize|compare)\b', text, re.I):
        return 'en'
    return 'en' if re.search(r'[A-Za-z]{2,}', text) and not re.search(r'[\u3400-\u9fff]', text) else 'zh'


def t(text, lang=None):
    text = str(text or '')
    target = _normalize(lang) or language()
    if target == 'zh':
        return next((source for source, english in CATALOG.items() if english == text), text)
    if text in CATALOG:
        return CATALOG[text]
    for pattern, render in _PATTERNS:
        match = re.fullmatch(pattern, text)
        if match:
            return render(match)
    # Only translate exact catalog entries within generated metadata rows.
    for separator in ('\n', ' · ', ' → '):
        if separator in text:
            return separator.join(t(part, target) for part in text.split(separator))
    return text


def localize_widgets(root, lang=None):
    """Update product attributes on the GUI thread, retaining their source text.

    Set ``i18nSkip=True`` on raw-content widgets or containers. Editor bodies
    and QTextBrowser records are never read or rewritten.
    """
    from PySide6.QtWidgets import (QWidget, QLabel, QAbstractButton, QComboBox,
                                  QTabWidget, QLineEdit, QPlainTextEdit, QTextEdit,
                                  QGroupBox)
    target = _normalize(lang) or language()

    def skipped(widget):
        current = widget
        while current is not None:
            if current.property('i18nSkip'):
                return True
            if current is root:
                break
            current = current.parent()
        return False

    def update(widget, key, current, setter):
        cache = getattr(widget, '_sessionlens_i18n', {})
        old = cache.get(key)
        source = old[0] if old and current == old[1] else current
        output = t(source, target)
        cache[key] = (source, output)
        widget._sessionlens_i18n = cache
        if output != current:
            setter(output)

    for widget in [root, *root.findChildren(QWidget)]:
        if skipped(widget):
            continue
        if widget.windowTitle():
            update(widget, 'window', widget.windowTitle(), widget.setWindowTitle)
        if isinstance(widget, (QLabel, QAbstractButton)):
            update(widget, 'text', widget.text(), widget.setText)
        if isinstance(widget, QGroupBox):
            update(widget, 'title', widget.title(), widget.setTitle)
        if isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit)):
            update(widget, 'placeholder', widget.placeholderText(), widget.setPlaceholderText)
        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                update(widget, ('item', index), widget.itemText(index), lambda value, i=index: widget.setItemText(i, value))
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                update(widget, ('tab', index), widget.tabText(index), lambda value, i=index: widget.setTabText(i, value))
