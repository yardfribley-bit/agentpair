/* Data center: read-only evidence search and explicitly requested, scoped investigation. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), arr = x => Array.isArray(x) ? x : [];
  const text = x => typeof x === 'string' ? x : typeof x === 'number' ? String(x) : '';
  const el = (tag, value, cls) => {const n = document.createElement(tag); if (value != null) n.textContent = text(value); if (cls) n.className = cls; return n;};
  const put = (id, value) => {$(id).textContent = text(value);};
  const option = (value,label) => {const n = el('option',label); n.value = value; return n;};
  const kinds = {user:'用户输入',user_message:'用户输入',context:'模型上下文',tool_call:'工具调用',tool_result:'工具返回',reasoning:'已记录思路',reply:'Agent 回复',assistant_message:'Agent 回复',http:'HTTP 证据'};
  const appName = value => ({workbuddy:'WorkBuddy',codex:'Codex'}[text(value).toLowerCase()] || text(value) || 'Agent 未确认');
  const collectorName = value => ({applens:'AppLens',sessionlens:'SessionLens'}[value] || text(value) || '来源未确认');
  const addressLabels = {captured_request_target:'捕获请求中的目的地',tool_argument_target:'工具参数中的访问对象',text_mention:'正文提及的地址',not_collected:'目的地未采集'};
  const basisLabels = {recorded_source_parent:'原始父消息关联',recorded_source_relation:'原记录有明确关联',selected_source_record:'当前来源记录',same_scope_time_neighborhood:'邻近记录，尚未确认归属',recorded_unique_call_id:'唯一调用标识配对',recorded_unique_parent_id:'唯一原始父消息标识',recorded_turn_id:'原始沟通轮次标识',recorded_unique_turn_id:'唯一原始沟通轮次标识',inferred_source_time:'前序记录候选，未确认归属',context_record_receipt:'平台保留上下文记录接收时间',not_collected_per_event:'未采集每个事件的独立接收时间'};
  const fieldLabels = {command:'执行命令',cmd:'执行命令',code:'执行代码',cwd:'执行目录',exitCode:'退出码',exit_code:'退出码',stdout:'标准输出',stderr:'错误输出',items:'返回条目',data:'返回数据',content:'返回内容',text:'正文',prompt:'生成提示词',duration:'时长',seconds:'时长（秒）',resolution:'分辨率',aspect_ratio:'画面比例',enable_audio:'生成音频',negative_prompt:'反向提示词',image_url:'输入图片',output_dir:'输出目录',toolName:'实际工具',params:'参数',status:'工具返回状态',mode:'生成模式',type:'返回类型',path:'文件路径',url:'访问地址',layer:'采集层',receivedBasis:'接收时间依据',sourceSessionId:'源 Session 标识',bodySHA256:'正文校验值',integrityEvidence:'完整性证据',wireLengthMatched:'传输长度核对',recordStatus:'源记录状态',truncated:'源记录是否截断'};
  const stageLabels = {queued:'问题已接收，等待检索',indexing:'正在更新已采集记录索引',retrieving:'正在查找相关证据',linking:'正在核对需求与行为的关联',analyzing:'正在分析观察到的行为和证据',reviewing:'正在复核回答与原文'};
  // This dictionary mirrors the 17 accepted query fields, not values guessed from historical data.
  const searchGroups = [{id:'scope',label:'设备与来源'},{id:'content',label:'行为与内容'},{id:'capability',label:'工具与能力'},{id:'address',label:'访问与提及的地址'}];
  const searchFields = [
    {name:'device',label:'设备',group:'scope',description:'按完整设备名称或设备标识，查这台机器上报的记录。',format:'device="设备完整名称或标识"'},
    {name:'app',label:'Agent',group:'scope',description:'查指定 Agent 的已上传记录。',values:[{value:'codex',label:'Codex',description:'Codex 的已上传会话和上下文记录。'},{value:'workbuddy',label:'WorkBuddy',description:'WorkBuddy 的已上传会话和上下文记录。'}]},
    {name:'collector',label:'采集来源',group:'scope',description:'按上报数据的采集器筛选。',values:[{value:'applens',label:'AppLens',description:'AppLens 上报的模型输入、上下文及捕获请求。'},{value:'sessionlens',label:'SessionLens',description:'SessionLens 上报的提问、思路、工具动作和回复。'}]},
    {name:'session',label:'来源会话',group:'scope',description:'查同一个原始来源会话的记录；来源会话可能包含多个需求。',format:'session="完整来源会话标识"'},
    {name:'kind',label:'行为类型',group:'content',description:'按记录发生的行为筛选，下面是支持的 7 种类型。',values:[
      {value:'user',label:'用户提问',description:'用户向 Agent 提出的需求、补充和追问。'},
      {value:'context',label:'模型上下文',description:'采集到的上下文材料及装配记录。'},
      {value:'tool_call',label:'工具调用',description:'Agent 请求执行工具的名称、参数和提示词。'},
      {value:'tool_result',label:'工具返回',description:'工具返回的内容、输出及执行状态。'},
      {value:'reasoning',label:'已记录思路',description:'查看来源日志保留的 Agent 思路。'},
      {value:'reply',label:'Agent 回复',description:'Agent 向用户返回的回答和交付说明。'},
      {value:'http',label:'捕获的模型请求',description:'查看 AppLens 捕获的模型请求及正文。'}]},
    {name:'location',label:'内容位置',group:'content',description:'按记录中保留的内容位置筛选；不等于全文只搜索这个位置。',values:[
      {value:'user',label:'用户提问',description:'记录包含用户提问位置的内容。'},
      {value:'context',label:'上下文正文',description:'记录包含模型上下文位置的内容。'},
      {value:'arguments',label:'工具参数',description:'记录包含工具参数、命令或提示词。'},
      {value:'result',label:'工具返回',description:'记录包含工具返回内容。'},
      {value:'reply',label:'Agent 回复',description:'记录包含回复位置的内容。'},
      {value:'metadata',label:'源记录字段',description:'记录包含源记录的辅助字段和标识。'}]},
    {name:'command',label:'执行命令',group:'content',description:'按完整命令或已识别的程序名匹配，例如 curl；不是命令片段包含匹配。',format:'command="curl"'},
    {name:'tool',label:'工具名称',group:'capability',description:'按原始工具名查看调用记录、参数和返回。',boundary:'此字段匹配已结构化识别的工具；AppLens 上下文里的工具正文可用关键词查找。',format:'tool="VideoGen"'},
    {name:'function',label:'函数名称',group:'capability',description:'按记录中已结构化识别的完整函数名称匹配。',format:'function="完整函数名称"'},
    {name:'executor',label:'执行器',group:'capability',description:'按已识别的执行器名称匹配，如工具的包装执行器。',format:'executor="完整执行器名称"'},
    {name:'skill',label:'Skill',group:'capability',description:'查看读取或加载这个 Skill 的记录。',format:'skill="taste-skill"'},
    {name:'mcp',label:'MCP 服务',group:'capability',description:'按已识别的 MCP 服务名或服务别名匹配。',format:'mcp="tinyfish"'},
    {name:'mcp_method',label:'MCP 方法',group:'capability',description:'查看指定 MCP 方法的调用记录。',boundary:'与 mcp 组合只限定同一记录，具体嵌套调用需核对原文。',format:'mcp_method="完整方法名称"'},
    {name:'ip',label:'正文提及 IP',group:'address',description:'查已保留正文中提及的完整 IPv4 或 IPv6 地址。',format:'ip="192.0.2.1"'},
    {name:'domain',label:'正文提及域名',group:'address',description:'查已保留正文中出现的完整域名，不包含协议或路径。',format:'domain="wttr.in"'},
    {name:'target_ip',label:'目标 IP',group:'address',description:'查请求或工具参数中识别的完整目标 IP。',boundary:'连接结果请核对对应返回。',format:'target_ip="192.0.2.1"'},
    {name:'target_domain',label:'目标域名',group:'address',description:'查请求或工具参数中识别的目标域名。',boundary:'访问结果请核对对应返回。',format:'target_domain="wttr.in"'}
  ];
  const searchFieldMap = new Map(searchFields.map(field => [field.name,field]));
  let completionItems = [], completionIndex = -1, completionState = null, composingQuery = false;
  function closeCompletions() {
    completionItems = []; completionIndex = -1; completionState = null;
    $('dc-completions').hidden = true; $('dc-query').setAttribute('aria-expanded','false'); $('dc-query').setAttribute('aria-activedescendant','');
  }
  function queryTokens(value, end) {
    const tokens = []; let i = 0;
    while (i < end) {
      if (/\s/.test(value[i])) {i++; continue;}
      const start = i, pair = value.slice(i,i+2);
      if (['&&','||','!='].includes(pair)) {tokens.push({value:pair,start,end:i+2,type:'operator'}); i+=2; continue;}
      if (value[i] === '=') {tokens.push({value:'=',start,end:++i,type:'operator'}); continue;}
      if (value[i] === '"' || value[i] === "'") {
        const quote = value[i++]; let content = '', closed = false;
        while (i < end) {const ch = value[i++]; if (ch === '\\' && i < end) content+=value[i++]; else if (ch === quote) {closed=true;break;} else content+=ch;}
        tokens.push({value:content,start,end:i,type:'quoted',quote,closed}); continue;
      }
      while (i < end && !/[\s=!&|"']/.test(value[i])) i++;
      if (i === start) {tokens.push({value:value[i],start,end:++i,type:'other'}); continue;}
      tokens.push({value:value.slice(start,i),start,end:i,type:'word'});
    }
    return tokens;
  }
  function valueTokenEnd(query, start, quote) {
    let i = start;
    if (quote && query[i] === quote) {
      i++;
      while (i < query.length) {if (query[i] === '\\') i+=2; else if (query[i++] === quote) break;}
      return Math.min(i,query.length);
    }
    while (i < query.length && !/[\s&|=!]/.test(query[i])) i++;
    return i;
  }
  function completionContext() {
    const input = $('dc-query'), query = input.value, cursor = Number.isInteger(input.selectionStart) ? input.selectionStart : query.length;
    const tokens = queryTokens(query,cursor), last = tokens.at(-1), prev = tokens.at(-2), before = tokens.at(-3);
    if (last && ['=','!='].includes(last.value) && prev?.type === 'word' && searchFieldMap.has(prev.value.toLowerCase())) {
      let start = cursor; while (/\s/.test(query[start] || '') && start < query.length) start++;
      const quote = ['"',"'"].includes(query[start]) ? query[start] : '"';
      return {type:'value',field:searchFieldMap.get(prev.value.toLowerCase()),prefix:'',start,end:valueTokenEnd(query,start,query[start] === quote ? quote : ''),quote,operator:last.value};
    }
    if (last && prev && ['=','!='].includes(prev.value) && before?.type === 'word' && searchFieldMap.has(before.value.toLowerCase())) {
      const state = {type:'value',field:searchFieldMap.get(before.value.toLowerCase()),prefix:last.value,start:last.start,end:valueTokenEnd(query,last.start,last.quote),quote:last.quote || '"',operator:prev.value};
      return last.end < cursor || last.type === 'quoted' && last.closed ? {...state,type:'complete'} : state;
    }
    if (last?.type === 'word' && last.end === cursor && /^[a-z_]+$/i.test(last.value) && !['=','!='].includes(prev?.value)) {
      let end = cursor; while (/[a-z_]/i.test(query[end] || '') && end < query.length) end++;
      const existingOperator = query.slice(end).match(/^\s*(!?=)/);
      return {type:'field',prefix:last.value.toLowerCase(),start:last.start,end:end+(existingOperator?.[0].length || 0),operator:existingOperator?.[1] || '='};
    }
    if (last && ['&&','||'].includes(last.value)) return {type:'field',prefix:'',start:cursor,end:cursor,operator:'='};
    return null;
  }
  function guidanceFor(state) {
    if (!state?.field) return '关键词搜索已上传正文；条件按完整值匹配，command 也支持程序名。';
    const field = state.field;
    return field.name + ' · ' + field.description + (field.boundary ? ' '+field.boundary : '') + (field.format ? ' 格式示例：' + field.format : '') + (state.operator === '!=' ? ' 排除该值，也会包含缺少该字段的记录。' : '');
  }
  function highlightCompletion(index) {
    completionIndex = index;
    const nodes = [...$('dc-completion-list').children];
    for (let i=0;i<nodes.length;i++) {nodes[i].classList.toggle('active',i === index); nodes[i].setAttribute('aria-selected',String(i === index));}
    $('dc-query').setAttribute('aria-activedescendant',index >= 0 ? 'dc-completion-'+index : '');
    if (index >= 0) nodes[index]?.scrollIntoView({block:'nearest'});
  }
  function showCompletions(forceFields = false) {
    if (mode !== 'search' || composingQuery) {closeCompletions(); return;}
    let state = completionContext();
    if (!state && forceFields) {const input=$('dc-query'),cursor=Number.isInteger(input.selectionStart)?input.selectionStart:input.value.length, last=queryTokens(input.value,cursor).at(-1); if (last?.type !== 'quoted' || last.closed) state={type:'field',prefix:'',start:cursor,end:cursor,operator:'=',append:true};}
    put('dc-query-guidance',guidanceFor(state));
    let candidates = [];
    if (state?.type === 'field') candidates = searchFields.filter(field=>field.name.startsWith(state.prefix)).map(field=>({type:'field',field,label:field.label,value:field.name,description:field.description}));
    else if (state?.type === 'value' && state.field.values) candidates = state.field.values.filter(item=>!state.prefix || (item.value+' '+item.label).toLowerCase().includes(state.prefix.toLowerCase())).map(item=>({...item,type:'value',field:state.field}));
    if (!candidates.length) {closeCompletions(); if (state?.type === 'value' && state.field.values) put('dc-query-guidance',guidanceFor(state)+' 未找到这个值，请选择支持的类型或使用关键词搜索。'); return;}
    completionItems = candidates; completionState = state; completionIndex = -1;
    const buttons = candidates.map((item,index)=>{
      const n = el('button',null,'dc-completion-option'); n.type='button'; n.id='dc-completion-'+index; n.dataset.completionIndex=String(index); n.setAttribute('role','option');n.setAttribute('aria-selected','false');n.tabIndex=-1;
      const title=el('span',null,'dc-completion-option-title');title.append(el('code',item.type === 'field' ? item.value+'=' : item.value),el('strong',item.label));n.append(title,el('span',item.description,'dc-completion-option-description'));
      n.addEventListener('mousedown',e=>e.preventDefault()); n.addEventListener('click',()=>chooseCompletion(index)); return n;
    });
    $('dc-completion-list').replaceChildren(...buttons); put('dc-completion-title',state.type === 'value' ? state.field.name+' · '+state.field.label : '搜索字段 · '+candidates.length+' 项');
    put('dc-completion-help',state.type === 'value' ? '填入后点击“搜索数据”。候选是支持的类型，不代表当前一定有匹配记录。' : '选择字段查看可搜索的内容与填写格式。');
    $('dc-completions').hidden=false; $('dc-query').setAttribute('aria-expanded','true'); $('dc-query').setAttribute('aria-activedescendant','');
  }
  function updateQueryDraft() {
    resultViewOverride=''; $('dc-share-confirmed').checked=false;
    if (mode === 'investigate') {stopQuestion();$('dc-progress').hidden=true;$('dc-investigation-result').hidden=true;clearError();}
    saveDraft();
  }
  function replaceQuery(start,end,replacement,caret) {
    const input=$('dc-query'), value=input.value.slice(0,start)+replacement+input.value.slice(end);
    if (value.length > Number(input.maxLength || 2000)) {put('dc-query-guidance','查询最多 2000 字，请先缩短条件。');closeCompletions();return false;}
    input.value=value;input.focus();input.setSelectionRange?.(caret,caret);updateQueryDraft();return true;
  }
  function chooseCompletion(index) {
    const item=completionItems[index], state=completionState;
    if (!item || !state || composingQuery) return;
    const escaped=item.value.replace(/\\/g,'\\\\').replace(new RegExp(state.quote || '"','g'),'\\'+(state.quote || '"'));
    const prefix=state.append && state.start && !/\s|[&|]/.test($('dc-query').value[state.start-1]) ? ' && ' : '';
    const replacement=item.type === 'field' ? prefix+item.value+(state.operator || '=') : (state.quote || '"')+escaped+(state.quote || '"');
    if (!replaceQuery(state.start,state.end,replacement,state.start+replacement.length)) return;
    closeCompletions();if (item.type === 'field') showCompletions();else put('dc-query-guidance',guidanceFor({field:item.field,operator:state.operator}));
  }
  function insertSearchField(field) {
    if (mode !== 'search' || !field) return;
    const input=$('dc-query'), start=input.value.length, before=input.value;
    const prefix=before.trim() && !/(?:&&|\|\|)\s*$/.test(before) ? ' && ' : '';
    const replacement=prefix+field.name+'=';
    if (!replaceQuery(start,start,replacement,start+replacement.length)) return;
    $('dc-filter-panel').hidden=true;$('dc-add-filter').setAttribute('aria-expanded','false');showCompletions();
  }
  function renderSearchFields() {
    $('dc-field-groups').replaceChildren(...searchGroups.map(group=>{
      const section=el('section',null,'dc-field-group');section.append(el('h3',group.label));
      for (const field of searchFields.filter(item=>item.group === group.id)) {const button=el('button',null,'dc-field-button');button.type='button';button.dataset.searchField=field.name;button.append(el('code',field.name),el('span',field.label));button.title=field.description;button.addEventListener('click',()=>insertSearchField(field));section.append(button);}
      return section;
    }));
  }
  function renderActiveFilters() {
    const chips=[];
    for (const key of ['object','device','application','collector','kind','location']) {
      const input=$('dc-'+key); if (input.value !== 'all') chips.push(chip(({object:'能力类型',device:'设备',application:'Agent',collector:'来源',kind:'行为',location:'位置'}[key])+': '+(input.selectedOptions?.[0]?.textContent || input.value)));
    }
    for (const key of ['after','before']) if ($('dc-'+key).value) chips.push(chip((key === 'after'?'开始':'结束')+': '+$('dc-'+key).value.replace('T',' ')));
    $('dc-active-filters').replaceChildren(...chips);$('dc-active-filters').hidden=!chips.length;
  }
  const draftKey = 'agentpair.data-center.draft', pendingKey = 'agentpair.data-center.pending';
  let mode = 'search', session = {}, page = 1, searchGeneration = 0, searchController = null, lastSearch = null;
  let querySnapshot = '', querySignature = '';
  let resultViewOverride = '', lastSearchParams = null;
  const groupStates = new Map();
  let detailGeneration = 0, detailController = null, detailId = '', detailItem = null, detailTrigger = null, detailHistory = [];
  let questionGeneration = 0, questionRun = null, pendingQuestion = null, lastQuestion = null, previousQuestionId = '', previousDevice = '';
  let retryMode = 'search', identityReady = false;

  const formatted = value => typeof value === 'string' ? (() => {try {return JSON.stringify(JSON.parse(value),null,2);} catch (_) {return value;}})() : value == null ? '' : JSON.stringify(value,null,2);
  const short = (value,size = 240) => {const s = text(value); return s.length > size ? s.slice(0,size) + '…' : s;};
  function date(value) {
    if (value == null || value === '') return '未采集';
    let at = value; if (Number.isFinite(Number(value)) && !Number.isNaN(Number(value))) {at = Number(value); if (Math.abs(at) < 1e11) at *= 1000;}
    const d = new Date(at); return Number.isNaN(d.getTime()) ? '未采集' : new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(d);
  }
  function known(value, fallback = '未采集') {return text(value).trim() || fallback;}
  function operator(item) {const value = typeof item.operator === 'object' ? text(item.operator?.name) : text(item.operator); return !value || ['未确认','unknown'].includes(value) ? '操作人员未确认' : value;}
  function title(item) {return text(item.title).trim() || item.kindLabel || kinds[item.kind] || '已采集记录';}
  function basis(value) {if (typeof value !== 'string') return formatted(value); return basisLabels[value] || (/^[a-z]+_[a-z_]+$/.test(value) ? '关联依据见辅助字段，尚需核对' : value);}
  function observedSummary(item) {
    if (item.kind === 'tool_call') {const fields = ['记录调用 ' + known(item.tool || item.function,'未命名工具')]; if (item.tool && item.function) fields.push('函数为 ' + item.function); if (item.command) fields.push('命令：' + short(item.command,180)); if (item.destination) fields.push((addressLabels[item.addressBasis] || '涉及地址') + '：' + item.destination); return fields.join('；') + '。执行结果以对应返回为准。';}
    if (item.kind === 'tool_result') return '保留了 ' + known(item.tool || item.function,'工具') + ' 的返回记录。下方可核对对应调用、完整参数和返回。';
    if (item.addressBasis === 'captured_request_target') return '捕获请求中的目的地为 ' + item.destination + '。下方可核对已采请求内容；服务端接收与处理仍需独立证据。';
    if (item.kind === 'context') return '保留了一份上下文记录' + (arr(item.contextItems).length ? '，其中 ' + item.contextItems.length + ' 个输入块可展开核对' : '') + '。材料来源、出现位置和采集层见下方证据。';
    const snippet = text(item.excerpt).trim(); return title(item) + (snippet && !/^[{\[]/.test(snippet) && snippet !== title(item) ? '：' + short(snippet,240) : '。完整已采内容可在下方展开。');
  }
  function orderedRelations(relations, lookup) {
    const direct = relations.filter(r => r.relation !== 'preceding_user_candidate'), ids = [...new Set(direct.flatMap(r => [r.from,r.to]))], degree = new Map(ids.map(id => [id,0])), outgoing = new Map(ids.map(id => [id,[]]));
    for (const r of direct) {degree.set(r.to,degree.get(r.to)+1); outgoing.get(r.from).push(r.to);}
    const rank = new Map(), queue = ids.filter(id => degree.get(id) === 0);
    const kindRank = id => ({user:0,context:1,http:1,reasoning:2,tool_call:3,tool_result:4,reply:5}[lookup.get(id)?.kind] ?? 6);
    while (queue.length) {queue.sort((a,b) => kindRank(a)-kindRank(b)); const id = queue.shift(); rank.set(id,rank.size); for (const next of outgoing.get(id)) {degree.set(next,degree.get(next)-1); if (degree.get(next) === 0) queue.push(next);}}
    return relations.map((relation,index) => ({relation,index})).sort((a,b) => (rank.get(a.relation.from) ?? 1e6)-(rank.get(b.relation.from) ?? 1e6) || (rank.get(a.relation.to) ?? 1e6)-(rank.get(b.relation.to) ?? 1e6) || a.index-b.index).map(x => x.relation);
  }
  function chip(label, style = '') {return el('span',label,'dc-chip ' + style);}
  function error(message) {put('dc-error',message); $('dc-error').hidden = false;}
  function recover(message, kind = 'search') {retryMode = kind; put('dc-recovery-text',message); $('dc-recovery').hidden = false; put('dc-retry',kind === 'question' && pendingQuestion?.id ? '重新查看结果' : '重试');}
  function clearError() {$('dc-error').hidden = true; $('dc-recovery').hidden = true; put('dc-error','');}
  function saveDraft() {try {sessionStorage.setItem(draftKey,JSON.stringify({query:$('dc-query').value,mode,device:$('dc-device').value,object:$('dc-object').value,resultView:resultViewOverride,filters:Object.fromEntries(['application','collector','kind','location','after','before'].map(key=>[key,$('dc-'+key).value]))}));} catch (_) {}}
  function savePending(value) {pendingQuestion = value; try {value ? sessionStorage.setItem(pendingKey,JSON.stringify(value)) : sessionStorage.removeItem(pendingKey);} catch (_) {}}
  function localLink(rawUrl, fallback = '/model-data/raw') {
    try {const u = new URL(rawUrl || fallback,location.origin); if (u.origin === location.origin && u.pathname.startsWith('/')) return u.pathname + u.search + u.hash;} catch (_) {}
    return fallback;
  }
  function originalLink(record, label = '查看原始记录 ↗', device = '') {
    const fallback = '/model-data/raw?' + new URLSearchParams({scope:'global',device:record.deviceId || device || $('dc-device').value,collector:record.collector || (text(record.recordId).startsWith('sessionlens:') ? 'sessionlens' : 'applens'),request:record.recordId || record.id || ''});
    const n = el('a',label); n.href = localLink(record.rawUrl,fallback); n.target = '_blank'; n.rel = 'noopener'; return n;
  }
  async function api(path, payload, signal) {
    const controller = new AbortController(), abort = () => controller.abort();
    if (signal?.aborted) abort(); else signal?.addEventListener('abort',abort,{once:true});
    const timer = setTimeout(abort,30000);
    try {
      const response = await fetch(path,{method:payload ? 'POST' : 'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:payload ? {'Content-Type':'application/json','X-CSRF-Token':session.csrf || ''} : {},body:payload ? JSON.stringify(payload) : undefined});
      let data; try {data = await response.json();} catch (_) {throw Error('平台返回了无法读取的内容，请重试。');}
      if (!response.ok) {if (response.status === 401) {session = {}; renderIdentity();} throw Error(text(data.error) || '请求失败（HTTP ' + response.status + '）');}
      return data;
    } catch (e) {if (e.name === 'AbortError') throw Error(signal?.aborted ? '已停止本页等待' : '读取超时，请重试。'); throw e;}
    finally {clearTimeout(timer); signal?.removeEventListener('abort',abort);}
  }
  function renderIdentity() {
    put('dc-identity',session.username ? session.username + ' · 已登录' : '游客 · 可搜索原文');
    put('dc-auth-note',session.csrf ? '使用平台已配置的分析服务。只调查所选设备，不代表已完成全局语义调查。' : '自然语言调查需要登录。关键词与条件查询可直接使用。');
    $('dc-submit').disabled = mode === 'investigate' && (!identityReady || !session.csrf);
  }
  function renderScope() {
    const name = $('dc-device').selectedOptions?.[0]?.textContent || '所有设备';
    put('dc-investigation-scope',$('dc-device').value === 'all' ? '自然语言调查需要选择一台具体设备。当前不提供跨设备语义推断。' : '本次调查范围：' + name + ' · 该设备已上传的 AppLens 与 SessionLens 数据');
    put('dc-result-scope',name + ' · ' + ($('dc-collector').selectedOptions?.[0]?.textContent || '所有采集器'));
    const ranks = new URLSearchParams(); for (const key of ['device','application','collector']) if ($('dc-' + key).value !== 'all') ranks.set(key,$('dc-' + key).value); if (['tool','skill','mcp'].includes($('dc-object').value)) ranks.set('object',$('dc-object').value);
    for (const key of ['after','before']) if ($('dc-' + key).value) {const d = new Date($('dc-' + key).value); if (!Number.isNaN(d.getTime())) ranks.set(key,d.toISOString());}
    $('dc-rankings-link').href = '/model-data/rankings' + (ranks.size ? '?' + ranks : ''); renderActiveFilters();
  }
  function selectMode(next) {
    if (next !== mode) {stopQuestion(); searchController?.abort(); cancelGroupRequests(); ++searchGeneration; $('dc-progress').hidden = true; $('dc-share-confirmed').checked = false; clearError();}
    mode = next; closeCompletions(); $('dc-add-filter').hidden = mode !== 'search'; $('dc-field-groups').hidden = mode !== 'search';
    if (mode === 'investigate') {$('dc-filter-panel').hidden=false; $('dc-advanced-filters').open=true;} else {$('dc-filter-panel').hidden=true;}
    put('dc-filter-panel-title',mode === 'search'?'添加筛选条件':'调查范围'); put('dc-filter-panel-note',mode === 'search'?'选择字段填入搜索框；范围筛选按所选值生效':'自然语言调查目前只检索所选设备。'); put('dc-advanced-filters-title',mode === 'search'?'设置设备范围、时间与能力类型':'选择本次调查设备');
    $('dc-add-filter').setAttribute('aria-expanded','false'); put('dc-query-guidance',mode === 'search' ? '关键词搜索已上传正文；输入 kind= 查看支持的行为类型。' : '先在下方选择设备，再提出调查问题。');
    for (const name of ['search','investigate']) {$('dc-mode-' + name).classList.toggle('active',name === mode); $('dc-mode-' + name).setAttribute('aria-selected',String(name === mode));}
    $('dc-share-panel').hidden = mode !== 'investigate'; $('dc-search-results').hidden = mode !== 'search';
    $('dc-syntax').hidden = mode !== 'search';
    $('dc-investigation-result').hidden = true;
    put('dc-submit',mode === 'search' ? '搜索数据' : '发起调查');
    put('dc-mode-note',mode === 'search' ? '直接查询平台记录，不调用模型' : '先确认调查范围，再发送相关片段');
    put('dc-search-hint',mode === 'search' ? '条件作用于同一条记录；输入 kind=、tool= 或 skill= 查看说明。' : '可以询问资料去向、工具动作、需求关联及证据缺口，也可以继续追问。');
    $('dc-query').placeholder = mode === 'search' ? '输入关键词，或输入 kind= 查看可搜索的行为类型' : '例如：这台设备的哪些请求带入了服务器凭据？原文在哪里，能否确认目的地？';
    $('dc-query').required = mode === 'investigate';
    for (const key of ['object','application','collector','kind','location','after','before','apply-time']) $('dc-' + key).disabled = mode === 'investigate';
    renderIdentity(); renderScope(); saveDraft();
  }
  function resultView() {
    if (resultViewOverride) return resultViewOverride;
    return ['skill','mcp'].includes($('dc-object').value) || /(?:^|\s|&&|\|\|)(?:skill|mcp|mcp_method)\s*(?:!?=)/i.test($('dc-query').value) ? 'request' : 'record';
  }
  function renderResultView() {
    const grouped = resultView() === 'request';
    for (const view of ['request','record']) {$('dc-view-' + view).classList.toggle('active',view === resultView()); $('dc-view-' + view).setAttribute('aria-pressed',String(view === resultView()));}
    put('dc-view-note',grouped ? '先看用户输入，再展开匹配的调用记录；前序输入的任务归属仍需核对' : '每条匹配记录单独展示');
  }
  function cancelGroupRequests() {for (const state of groupStates.values()) {state.controller?.abort(); state.loading = false; putGroupProgress(state);}}
  function clearGroupStates() {cancelGroupRequests(); groupStates.clear();}
  function filterParams() {
    const p = {q:$('dc-query').value.trim(),object:$('dc-object').value,collector:$('dc-collector').value,application:$('dc-application').value,device:$('dc-device').value,kind:$('dc-kind').value,location:$('dc-location').value,page:String(page),pageSize:'20'};
    if (resultView() === 'request') p.group = 'request';
    for (const field of ['after','before']) {const input = $('dc-' + field).value; if (input) {const d = new Date(input); if (Number.isNaN(d.getTime())) throw Error('请填写有效的时间范围。'); p[field] = d.toISOString();}}
    if (p.after && p.before && p.after > p.before) throw Error('开始时间不能晚于结束时间。'); return p;
  }
  function renderFacets(facets) {
    if (!Array.isArray(facets?.devices)) return;
    const selected = $('dc-device').value, knownDevices = facets.devices;
    $('dc-device').replaceChildren(option('all','所有设备'),...knownDevices.map(d => option(d.id,d.name || d.id.slice(0,8))));
    if (selected !== 'all') {if (!knownDevices.some(d => d.id === selected)) $('dc-device').append(option(selected,selected)); $('dc-device').value = selected;}
    renderScope();
  }
  function coverageText(coverage, total = null) {
    if (typeof coverage === 'string') return coverage;
    const c = coverage || {}, fields = [];
    if (Number.isFinite(c.retainedRecords)) fields.push('平台保留 ' + c.retainedRecords.toLocaleString('zh-CN') + ' 条记录');
    if (Number.isFinite(c.searchedRecords)) fields.push('本次检索 ' + c.searchedRecords.toLocaleString('zh-CN') + ' 条');
    if (Number.isFinite(c.indexedRecords) && Number.isFinite(c.totalRecords)) fields.push('索引覆盖 ' + c.indexedRecords + ' / ' + c.totalRecords + ' 条');
    if (c.indexComplete === false) fields.push('历史索引尚在补充');
    if (Number.isFinite(c.selectedRecords)) fields.push('回答核对 ' + c.selectedRecords + ' 个片段');
    if (total !== null) fields.push('命中数按采集记录计，不等于风险事件数或完整任务数');
    return fields.join(' · ');
  }
  function renderCoverageDetails(coverage) {
    const c = coverage || {}, items = [...arr(c.sources).map(s => collectorName(s.collector) + '：' + Number(s.total || 0).toLocaleString('zh-CN') + ' 条保留记录'),...arr(c.limitations).map(x => typeof x === 'string' ? x : formatted(x))];
    if (c.fullRetainedText === true) items.unshift('关键词和条件查询覆盖平台保留的正文；不代表终端所有行为均已采集。');
    $('dc-coverage-details').hidden = !items.length;
    $('dc-coverage-list').replaceChildren(...items.map(x => el('li',x)));
  }
  function highlight(parent, content, query) {
    const s = text(content), q = query && !/[=<>!&|]/.test(query) ? query : '';
    let at = q ? s.toLocaleLowerCase().indexOf(q.toLocaleLowerCase()) : -1;
    if (q && (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(q) || /^(?:[a-z0-9-]+\.)+[a-z]{2,}$/i.test(q))) {
      const escaped = q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'), match = new RegExp('(^|[^a-z0-9_.-])' + escaped + '(?![a-z0-9_.-])','i').exec(s);
      at = match ? match.index + match[1].length : -1;
    }
    if (at < 0) parent.textContent = s;
    else parent.append(document.createTextNode(s.slice(0,at)),el('mark',s.slice(at,at+q.length)),document.createTextNode(s.slice(at+q.length)));
  }
  function matchBlock(match, query) {
    const n = el('div',null,'dc-match'); n.append(el('span',match.label || match.field || '命中原文','dc-match-label'));
    if (match.addressBasis) n.append(el('span',(match.matchedAddress ? '命中地址：' + match.matchedAddress + ' · ' : '') + (addressLabels[match.addressBasis] || '地址命中依据待核对'),'dc-match-address'));
    const p = el('div'); highlight(p,match.excerpt,query); n.append(p); return n;
  }
  function readableScalar(value) {if (typeof value === 'boolean') return value ? '开启' : '关闭'; return value == null ? '未采集' : typeof value === 'object' ? formatted(value) : String(value);}
  function outcomeClass(status) {return ['completed','success','succeeded','ok','exit_zero'].includes(status) ? 'success' : ['failed','error','failure','exit_nonzero'].includes(status) ? 'danger' : 'attention';}
  function outcomeHeading(outcome) {return outcome?.label || (outcome?.status ? '工具返回：' + outcome.status : '返回待核对');}
  function appendFactItems(container, entries, cls = 'dc-brief-fields') {
    const list = el('div',null,cls); for (const f of arr(entries)) {if (f.value == null || f.value === '') continue; const n = el('span'); n.append(el('span',f.label || fieldLabels[f.key] || f.key || '参数','dc-field-name'),el('span',short(f.key === 'enable_audio' && ['true','false'].includes(String(f.value)) ? String(f.value) === 'true' ? '开启' : '关闭' : readableScalar(f.value),180))); list.append(n);} if (list.children.length) container.append(list);
  }
  function activityFor(item) {
    if (item.activity && ['skill_load','skill_read','mcp_call'].includes(item.activity.kind)) return item.activity;
    const capabilities = arr(item.capabilities).filter(c => ['skill','mcp'].includes(c.type) && text(c.name));
    if (!capabilities.length) return null;
    const c = capabilities[0], kind = c.type === 'mcp' ? 'mcp_call' : c.evidence === 'loaded' ? 'skill_load' : 'skill_read';
    const hasReturn = item.result != null || text(item.summary?.resultPreview || item.resultPreview) || ['success','ok','completed','exit_zero'].includes(item.summary?.outcome?.status);
    return {kind,name:c.name,method:c.method,title:kind === 'mcp_call' ? '调用 ' + c.name + (c.method ? ' · ' + c.method : '') : (kind === 'skill_load' ? '加载 ' : '读取 ') + c.name + (kind === 'skill_read' ? ' 说明' : ''),parameters:arr(item.summary?.parameters),request:item.taskContext ? {text:item.taskContext.userInput,...item.taskContext} : {},returnKind:c.evidence === 'wrapped' ? 'wrapper_result' : !hasReturn ? 'missing' : c.type === 'skill' ? 'instructions' : 'tool_result',calls:[]};
  }
  function activityLabel(activity) {return {skill_load:'Skill 加载',skill_read:'Skill 说明读取',mcp_call:'MCP 调用'}[activity?.kind] || '能力调用';}
  function activityWrapped(activity, item) {return activity?.returnKind === 'wrapper_result' || arr(activity?.calls).some(c => c.sourceOffset != null || c.wrapper || c.evidence === 'wrapped') || arr(item.capabilities).some(c => c.wrapper || c.evidence === 'wrapped');}
  function meaningfulRequest(value) {
    const s = text(value).trim(), compact = s.replace(/[\s，。！？、,.!?~～]/g,'');
    return compact.length > 3 && !/^(?:已经连接|已连接|连接好了|连接成功|好的|好啊|可以|可以了|继续|继续吧|完成了|已经完成|明白了|没问题|就这样|知道了|收到|确认|执行吧|开始吧|可以开始|可以执行|已经好了|可以继续|yes|ok|done|connected)$/i.test(compact);
  }
  function requestFor(item, activity = activityFor(item)) {
    const raw = item.request || activity?.request || item.portrait?.userRequest || item.taskContext || {}, value = text(raw.text || raw.userInput).trim();
    // Explicit backend anchors already passed request-boundary checks. Do not
    // discard legitimate short inputs such as “查天气” again in presentation.
    const hasInput = item.request || raw.inputKind === 'continuation' ? Boolean(value) : meaningfulRequest(value);
    return {...raw,text:hasInput ? value : '',association:hasInput && ['recorded','candidate'].includes(raw.association) ? raw.association : 'unknown',sourceText:value};
  }
  function requestAssociationLabel(request) {return request.basis === 'recorded_root_turn_parent_session' && request.association === 'recorded' ? '主会话原文关联' : '原记录关联';}
  function requestDiagnostic(request) {
    const value = text(request.reason || request.basis), labels = {recorded_root_turn_parent_session:'已通过父会话与沟通轮次标识找到主会话的用户输入。',missing_turn_user:'对应轮次的用户输入尚未采集。',ambiguous_turn:'同一轮次存在多条用户输入，尚不能唯一关联。',ambiguous_turn_user:'对应轮次存在多条用户输入，尚不能唯一关联。',no_preceding_user:'未找到可对应的前序用户输入。',not_collected:'尚未采集到可关联的用户输入。',no_user_input:'来源记录未提供可关联的用户输入。'};
    return labels[value] || basisLabels[value] || (/^[a-z]+_[a-z_]+$/.test(value) ? '尚未找到唯一对应的用户输入，可查看原始记录核对关联依据。' : value);
  }
  function appendContextHint(container, request) {
    const hint = request.contextHint || {}, hintText = text(hint.text || hint.userInput).trim(); if (!hintText) return;
    const section = el('section',null,'dc-request-context-hint'), heading = el('div',null,'dc-request-label'); heading.append(el('span','前序需求线索'),chip('候选线索','attention')); section.append(heading,el('p',short(hintText,300),'dc-card-request-text'),el('p','这是前序输入线索，尚未确认与本轮属于同一任务。','dc-task-association'));
    if (hint.recordId) {const actions = el('div',null,'dc-group-bottom'), button = el('button','查看前序输入原文 →','dc-group-source'); button.type = 'button'; button.setAttribute('aria-label','查看前序需求线索的原始用户输入'); button.addEventListener('click',event => {event.stopPropagation(); openDetail(hint.recordId,button);}); actions.append(button); section.append(actions);} container.append(section);
  }
  function appendRequest(container, request, compact = false) {
    const heading = el('div',null,'dc-request-label'); if (compact) heading.append(el('span',request.association === 'unknown' ? '用户输入未关联' : request.inputKind === 'continuation' ? '本轮用户输入（续接）' : '用户输入'));
    if (request.association === 'candidate' && request.text) heading.append(chip('候选关联','attention'));
    else if (request.association === 'recorded' && request.text) heading.append(chip(requestAssociationLabel(request),'blue'));
    if (heading.children.length) container.append(heading); container.append(el('p',request.text || (compact ? '未找到对应用户输入' : '尚未关联到可确认的完整需求。'),compact ? 'dc-card-request-text' : 'dc-task-request'));
    if (!request.text && (request.reason || request.basis)) container.append(el('p',requestDiagnostic(request),'dc-task-association'));
    if (request.inputKind === 'continuation' && request.text) container.append(el('p','本轮保留继续或确认消息的原文；这句话本身不代表完整需求。','dc-task-association'));
    appendContextHint(container,request);
    if (!compact) {
      container.append(el('p',request.association === 'recorded' ? request.basis === 'recorded_root_turn_parent_session' ? '用户输入来自主会话的原始记录。' : '用户输入有原记录关联。' : request.association === 'candidate' ? '候选需求 · 根据前序记录找到，尚未确认属于同一任务。' : '任务归属尚未确认。','dc-task-association'));
      if (request.text && request.basis) container.append(el('p',requestDiagnostic(request),'dc-coverage'));
      if (request.limitation) container.append(el('p',request.limitation,'dc-coverage'));
      if (!request.text && request.sourceText) {const confirmation = el('details',null,'dc-inline-details'); confirmation.append(el('summary','已关联的用户短句 · 无法单独还原完整需求'),el('p',request.sourceText,'dc-value-text')); container.append(confirmation);}
    }
  }
  function activityReturnSummary(activity) {
    if (text(activity.returnSummary)) return activity.returnSummary;
    if (activity.returnKind === 'instructions') return '已取得 ' + known(activity.name,'Skill') + ' 的操作说明。';
    if (activity.returnKind === 'wrapper_result') return '已记录外层工具返回；子调用的独立返回尚需核对。';
    if (activity.returnKind === 'missing') return '未采集到可唯一对应的返回。';
    return '已保留工具返回，完整内容可在详情核对。';
  }
  function actionName(item) {const activity = activityFor(item); if (activity?.title) return short(activity.title,180); const execution = executionFor(item); if (execution) return short(execution.outer?.summary || arr(execution.operations).map(operation => [operation.action,operation.objectSummary].filter(Boolean).join(' · ')).join('；') || '执行内容尚待解析',180); const value = text(item.summary?.action); if (item.command && value === item.command) return '执行命令 · ' + known(item.tool || item.function,'Agent 工具'); if (item.tool && value === item.tool) return '调用工具 · ' + item.tool; return short(value || title(item),180);}
  const weatherDescriptions = {'clear':'晴','sunny':'晴','partly cloudy':'局部多云','cloudy':'多云','overcast':'阴','mist':'薄雾','fog':'雾','freezing fog':'冻雾','haze':'霾','patchy rain nearby':'附近局部降雨','patchy rain possible':'可能有局部降雨','patchy light rain':'局部小雨','light drizzle':'小毛毛雨','patchy light drizzle':'局部毛毛雨','light rain':'小雨','moderate rain':'中雨','heavy rain':'大雨','moderate or heavy rain shower':'中到大阵雨','thundery outbreaks in nearby':'附近雷雨','thunderstorm':'雷暴','snow':'雪','light snow':'小雪','moderate snow':'中雪','heavy snow':'大雪','blizzard':'暴风雪'};
  function displayFactValue(fact) {const value = readableScalar(fact.value); if (fact.label !== '天气') return value; const translated = weatherDescriptions[value.trim().toLowerCase().replace(/\s+/g,' ')]; return translated ? translated + '（' + value + '）' : value;}
  function capabilityEvidence(capability) {if (capability.sourceOffset != null && capability.evidence === 'wrapped') return '外层工具包装调用'; return {loaded:'明确加载',read:'读取说明',direct:'直接调用',wrapped:'命令包装调用'}[capability.evidence] || '识别依据待核对';}
  function capabilityList(item, limit = Infinity) {
    const list = el('div',null,'dc-capabilities');
    const capabilities = arr(item.capabilities).filter(c => ['skill','mcp'].includes(c.type) && text(c.name));
    for (const capability of capabilities.slice(0,limit)) {const row = el('span',null,'dc-capability'); row.append(chip((capability.type === 'skill' ? 'Skill · ' : 'MCP · ') + capability.name,'blue'),el('span',capabilityEvidence(capability),'dc-capability-evidence')); if (capability.method) row.append(el('span',capability.method,'dc-capability-method')); list.append(row);}
    if (capabilities.length > limit) list.append(el('span','另 ' + (capabilities.length-limit) + ' 项能力可在详情核对','dc-capability-evidence'));
    return list;
  }
  function resultSummary(summary) {
    const facts = arr(summary?.outcome?.facts), files = arr(summary?.outcome?.outputs);
    let preview = text(summary?.resultPreview); for (const fact of facts) if (fact.label === '天气' && text(fact.value)) preview = preview.replace(fact.value,displayFactValue(fact));
    if (!preview && facts.length) preview = facts.map(f => (f.label || '') + ' ' + displayFactValue(f)).join(' · ');
    if (!files.length) return preview;
    const error = text(summary?.outcome?.error || (['failed','error','failure','exit_nonzero'].includes(summary?.outcome?.status) ? summary?.outcome?.message || preview : ''));
    const fileBrief = '工具返回 ' + files.length + ' 个文件：' + files.slice(0,2).map(o => short(text(o.path || o.url || o.label).split(/[\\/]/).pop(),100)).join('、');
    return [error ? '错误：' + short(error,180) : facts.length ? preview : '',fileBrief].filter(Boolean).join('；');
  }
  function executionFor(item) {const execution = item.execution; return execution && typeof execution === 'object' && (arr(execution.operations).length || arr(execution.returns).length || execution.coverage?.incomplete) ? execution : null;}
  const executionFieldLabels = {code:'代码内容',cmd:'执行命令',cwd:'执行目录',output:'返回内容',result:'返回结果',status:'返回状态',summary:'结果摘要',preview:'已采集内容片段',exitCode:'退出码',durationSeconds:'耗时（秒）',contentType:'内容类型',function:'内部函数',sourceOffset:'源码起始位置',sourceEnd:'源码结束位置',sourceBasis:'识别依据',argumentsTruncated:'参数是否截取',operationsIdentified:'已识别的操作数',operationsCapped:'操作列表是否达到展示上限',sourceTruncated:'来源代码是否截取',returnTruncated:'返回是否截取',unpairedReturns:'未逐项配对的返回数',limitations:'待核对项',chunkId:'输出块标识',callId:'外层调用标识'};
  const executionTechnicalKeys = new Set(['i','index','chunkId','chunk_id','wall_time_seconds','session_id','original_token_count','sourceOffset','sourceEnd','sourceBasis']);
  function executionReadable(value, depth = 0) {
    const container = el('div',null,'dc-execution-readable');
    if (value == null || value === '') {container.append(el('p','未采集','dc-coverage')); return container;}
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (depth < 4 && /^[\[{]/.test(trimmed)) {try {const parsed = JSON.parse(trimmed); if (parsed && typeof parsed === 'object') return executionReadable(parsed,depth+1);} catch (_) {}}
      container.append(el('p',value,'dc-value-text')); return container;
    }
    if (Array.isArray(value)) {
      for (const [index,entry] of value.entries()) {const row = el('section',null,'dc-execution-array-item'); if (value.length > 1) row.append(el('h6','结果 ' + (index+1))); row.append(depth >= 4 ? el('pre',formatted(entry)) : executionReadable(entry,depth+1)); container.append(row);} return container;
    }
    if (typeof value !== 'object') {container.append(el('p',readableScalar(value),'dc-value-text')); return container;}
    const normal = Object.entries(value).filter(([key]) => !executionTechnicalKeys.has(key)), technical = Object.entries(value).filter(([key]) => executionTechnicalKeys.has(key)), list = el('dl',null,'dc-execution-fields');
    for (const [key,entry] of normal) {const label = el('dt',executionFieldLabels[key] || fieldLabels[key] || key), cell = el('dd'); if (depth >= 4) cell.append(el('pre',formatted(entry))); else cell.append(executionReadable(entry,depth+1)); list.append(label,cell);} if (normal.length) container.append(list);
    if (technical.length) {const more = el('details',null,'dc-inline-details dc-execution-technical-fields'); more.append(el('summary','运行辅助字段'),readable(Object.fromEntries(technical))); container.append(more);} return container;
  }
  function executionReturn(returned, compact = false) {
    const section = el('section',null,'dc-execution-return'), heading = el('div',null,'dc-execution-return-heading');
    const label = returned.contentType === 'code' ? '返回的代码' : returned.contentType === 'file_content' ? '读取到的文件内容' : '返回结果';
    heading.append(el('strong',label));
    if (returned.statusLabel || returned.status && returned.status !== 'unknown') heading.append(chip(returned.statusLabel || returned.status,outcomeClass(returned.status)));
    if (returned.exitCode != null) heading.append(el('span','退出码 ' + returned.exitCode,'dc-execution-status')); section.append(heading);
    const summary = text(returned.summary || returned.preview); if (summary) section.append(el('p',short(summary,compact ? 240 : 600),'dc-execution-result-summary'));
    const preview = text(returned.preview); if (preview && preview !== summary && !['code','file_content'].includes(returned.contentType)) {let parsed; try {parsed = JSON.parse(preview);} catch (_) {} if (parsed && typeof parsed === 'object') section.append(executionReadable(parsed)); else section.append(el('p',short(preview,compact ? 260 : 600),'dc-execution-result-preview'));}
    if (returned.contentType === 'code') section.append(el('p','返回包含代码；是否实际运行需对应执行记录。','dc-coverage'));
    const content = returned.content ?? returned.preview;
    if (content != null && content !== '') {const details = el('details',null,'dc-inline-details dc-execution-content'); details.append(el('summary',returned.contentType === 'code' ? '展开返回的代码' : returned.contentType === 'file_content' ? '展开已读取的文件内容' : '展开已采集的返回内容'),returned.contentType === 'code' ? el('pre',typeof content === 'string' ? content : formatted(content)) : executionReadable(content)); section.append(details);}
    if (returned.truncated) section.append(el('span','返回为已采集片段，完整原文可在记录详情核对。','dc-execution-limit'));
    return section;
  }
  function executionView(item, options = {}) {
    const execution = executionFor(item), {compact = false,showReturns = true,showTechnical = true} = options; if (!execution) return null;
    const section = el('section',null,'dc-execution' + (compact ? ' compact' : '')), operations = arr(execution.operations), returns = arr(execution.returns), title = el('div',null,'dc-execution-heading');
    title.append(el('h4',showReturns ? '内部操作与结果' : '内部操作（从外层代码识别）')); const outerMeta = el('div',null,'dc-execution-outer-status'); if (operations.length) outerMeta.append(el('span',operations.length + ' 项已识别操作','dc-label')); const outer = execution.outer || {}; if (outer.status === 'running' || outer.status === 'completed') outerMeta.append(chip(outer.status === 'running' ? '外层脚本仍在运行' : '外层脚本已完成','blue')); const duration = Number(outer.durationSeconds); if (outer.durationSeconds != null && Number.isFinite(duration) && duration >= 0) outerMeta.append(el('span','耗时 ' + duration + ' 秒','dc-label')); if (outerMeta.children.length) title.append(outerMeta); section.append(title);
    const deferred = !operations.length && !returns.length && execution.coverage?.incomplete;
    if (deferred) section.append(el('p','当前页尚未解析执行内容，可查看完整信息。','dc-execution-association'));
    const list = el('div',null,'dc-execution-operations'), additionalOperations = compact && operations.length > 3 ? el('details',null,'dc-inline-details dc-execution-more-operations') : null;
    if (additionalOperations) additionalOperations.append(el('summary','另 ' + (operations.length-3) + ' 项已识别操作'));
    for (const [index,operation] of operations.entries()) {
      const node = el('article',null,'dc-execution-operation'), heading = el('div',null,'dc-execution-action');
      if (operations.length > 1) heading.append(el('span',String(index+1),'dc-execution-number')); heading.append(el('strong',operation.action || '调用内部工具'));
      const targets = arr(operation.targets).filter(target => text(target.value)), object = text(operation.objectSummary) || targets.map(target => target.value).join('、'); if (object) heading.append(el('span','→','dc-execution-arrow'),el('span',object,'dc-execution-object')); node.append(heading);
      if (targets.length) {const fields = el('div',null,'dc-execution-targets'); for (const target of targets) {if (target.value === object && targets.length === 1) continue; const field = el('span'); field.append(el('span',target.label || (target.kind === 'url' ? '访问地址' : target.kind === 'file' ? '文件' : '操作对象'),'dc-field-name'),el('span',target.value)); fields.append(field);} if (fields.children.length) node.append(fields);}
      if (operation.function) node.append(el('p',operation.function,'dc-execution-function'));
      if (showReturns && operation.return?.association === 'outer_single_operation') {node.append(executionReturn(operation.return,compact),el('p','与外层单项操作对应；未采集内部调用标识。','dc-execution-association'));}
      else if (showReturns) node.append(el('p',execution.coverage?.returnProjectionUnavailable ? '已有外层返回，本页未展开；记录详情可查看。' : returns.length ? '独立结果尚未配对，见下方外层返回。' : '未采集内部调用的独立返回。','dc-execution-association'));
      const parameters = operation.arguments ?? (operation.command ? {cmd:operation.command,...(operation.cwd ? {cwd:operation.cwd} : {})} : null);
      if (parameters != null) {const more = el('details',null,'dc-inline-details dc-execution-parameters'); more.append(el('summary','查看命令与参数'),executionReadable(parameters)); if (operation.argumentsTruncated) more.append(el('p','当前参数为片段，可在原始记录中核对已采集全文。','dc-coverage')); node.append(more);} (additionalOperations && index >= 3 ? additionalOperations : list).append(node);
    }
    if (additionalOperations) list.append(additionalOperations);
    section.append(list);
    if (showReturns) {
      const paired = operations.some(operation => operation.return?.association === 'outer_single_operation'), outerReturns = returns.filter(returned => !paired || returned.association !== 'outer_single_operation');
      if (outerReturns.length) {const outer = el('section',null,'dc-execution-outer-returns'), additionalReturns = compact && outerReturns.length > 2 ? el('details',null,'dc-inline-details dc-execution-more-returns') : null; outer.append(el('h5',operations.length && !paired ? '外层返回 · 未逐项配对' : '外层返回')); if (additionalReturns) additionalReturns.append(el('summary','另 ' + (outerReturns.length-2) + ' 份外层返回')); for (const [index,returned] of outerReturns.entries()) (additionalReturns && index >= 2 ? additionalReturns : outer).append(executionReturn(returned,compact)); if (additionalReturns) outer.append(additionalReturns); section.append(outer);}
      else if (!paired && !deferred) section.append(el('p',execution.coverage?.returnProjectionUnavailable ? '已有外层返回，本页未展开；记录详情可查看。' : execution.outer?.status === 'running' ? '脚本仍在运行，等待后续返回。' : '未找到可对应的外层返回。','dc-execution-association'));
      const nonTextBlocks = Number(execution.coverage?.nonTextBlocks ?? execution.outer?.nonTextBlocks ?? returns.filter(returned => returned.contentType === 'non_text').length); if (Number.isFinite(nonTextBlocks) && nonTextBlocks > 0) section.append(el('p','另有 ' + nonTextBlocks + ' 份非文本返回，原文可查看。','dc-execution-limit'));
    }
    if (execution.coverage?.sourceTruncated || execution.coverage?.operationsCapped) section.append(el('p','操作按已采集代码识别，列表可能不完整。','dc-execution-limit'));
    if (showTechnical) {const technical = el('details',null,'dc-inline-details dc-execution-technical'); technical.append(el('summary','技术详情：外层调用、源码与采集边界'),readable(execution.outer),executionReadable({operations:operations.map(operation => ({function:operation.function,sourceOffset:operation.sourceOffset,sourceEnd:operation.sourceEnd,sourceBasis:operation.sourceBasis})),coverage:execution.coverage,returnFields:returns.map(returned => returned.technical || {})})); if (item.arguments != null) {technical.append(el('h5','原始外层调用参数'),executionReadable(item.arguments));} else if (arr(item.summary?.parameters).length) technical.append(executionReadable(Object.fromEntries(item.summary.parameters.map(parameter => [parameter.key || parameter.label,parameter.value])))); section.append(technical);}
    const keepInline = event => {for (let node = event.target; node && node !== section; node = node.parentElement) if (node.tagName === 'DETAILS') {event.stopPropagation(); break;}}; section.addEventListener('click',keepInline); section.addEventListener('keydown',keepInline);
    return section;
  }
  function groupedCount(data) {
    const count = Number(data.requestGroupTotal || 0).toLocaleString('zh-CN') + ' 项需求线索 · ' + Number(data.recordTotal || 0).toLocaleString('zh-CN') + ' 条匹配记录';
    return count + (data.unassignedRecordTotal ? ' · ' + Number(data.unassignedRecordTotal).toLocaleString('zh-CN') + ' 条归属未确认' : '');
  }
  function groupMember(item, index) {
    const summary = item.summary || {}, activity = activityFor(item), execution = executionFor(item), row = el('article',null,'dc-group-member');
    const heading = el('div',null,'dc-group-member-heading'); heading.append(el('span',String(index),'dc-group-member-number'),el('time',date(item.timestamp)),el('h4',actionName(item)));
    const request = requestFor(item,activity); if (request.association === 'candidate') heading.append(chip('候选关联','attention')); else if (request.association === 'unknown') heading.append(chip('归属未确认','attention'));
    row.append(heading);
    const body = el('div',null,'dc-group-member-body');
    if (execution && !activity) body.append(executionView(item,{compact:true}));
    else {
      if (!activity && (item.tool || item.function)) {const tool = el('p',null,'dc-group-member-tool'); tool.append(el('span','工具','dc-field-name'),el('strong',item.tool || item.function)); if (item.tool && item.function && item.tool !== item.function) tool.append(el('span','函数：' + item.function)); body.append(tool);}
      appendFactItems(body,arr(activity ? activity.parameters : summary.parameters).filter(f => f.key !== 'prompt').slice(0,3));
      if (item.command && !activity && !arr(summary.parameters).some(f => ['cmd','command'].includes(f.key))) appendFactItems(body,[{key:'command',value:item.command}]);
      if (!activity && summary.promptPreview) appendFactItems(body,[{key:'prompt',value:summary.promptPreview}]);
      const result = activity ? activityReturnSummary(activity) : resultSummary(summary) || (item.result != null ? readableScalar(item.result) : ['tool_call','tool_result'].includes(item.kind) ? '未找到可对应的工具返回，需核对原文。' : '');
      if (result) {const returned = el('p',null,'dc-group-member-return'); returned.append(el('span',activity?.returnKind === 'wrapper_result' ? '外层返回' : activity?.returnKind === 'instructions' ? '说明返回' : '返回摘要','dc-field-name'),el('span',short(result,220))); body.append(returned);}
      if (summary.outcome?.sourceTruncated) body.append(chip('返回被截取','attention'));
      if (execution && ['skill_read','skill_load'].includes(activity?.kind)) body.append(executionView(item,{compact:true,showReturns:false,showTechnical:false}));
    }
    const button = el('button','查看原文详情 →'); button.type = 'button'; button.setAttribute('aria-label','查看第 ' + index + ' 条匹配记录的原文详情：' + actionName(item)); button.addEventListener('click',() => openDetail(item.id,button));
    body.append(button); row.append(body); return row;
  }
  function renderGroupMembers(state) {
    state.list.replaceChildren(...state.items.map((item,index) => groupMember(item,index+1)));
    putGroupProgress(state);
  }
  function putGroupProgress(state) {
    state.position.textContent = '已展示 ' + state.items.length + ' / ' + state.group.recordCount + ' 条匹配记录 · 按时间从新到旧';
    state.more.hidden = !state.hasMore; state.more.disabled = state.loading;
    state.more.textContent = state.loading ? '正在读取…' : state.failed ? '重试加载更多' : '加载更多匹配记录';
  }
  async function loadGroupMembers(state) {
    if (state.loading || !state.hasMore || groupStates.get(state.group.id) !== state) return;
    state.loading = true; state.failed = false; state.error.hidden = true; state.controller = new AbortController(); putGroupProgress(state);
    const generation = searchGeneration, controller = state.controller;
    try {
      const params = {...state.params,group:'request',groupId:state.group.id,page:String(state.page+1),pageSize:'10'};
      const data = await api('/api/data-center/search?' + new URLSearchParams(params),undefined,controller.signal);
      if (generation !== searchGeneration || controller.signal.aborted || groupStates.get(state.group.id) !== state) return;
      const seen = new Set(state.items.map(item => item.id)); state.items.push(...arr(data.items).filter(item => !seen.has(item.id))); state.page = Number(data.page || state.page+1); state.hasMore = !!data.hasMore; state.loading = false; renderGroupMembers(state);
    } catch (e) {
      if (generation !== searchGeneration || controller.signal.aborted || groupStates.get(state.group.id) !== state) return;
      state.loading = false; state.failed = true; state.error.textContent = e.message; state.error.hidden = false; putGroupProgress(state);
    }
  }
  function requestGroupCard(group, query) {
    const request = group.request || {}, requestText = text(request.text).trim(), association = requestText && ['recorded','candidate'].includes(request.association) ? request.association : 'unknown';
    const card = el('article',null,'card dc-request-group'), heading = el('div',null,'dc-group-heading'), title = el('div');
    title.append(el('span',association === 'unknown' ? '用户输入未关联' : request.inputKind === 'continuation' ? '本轮用户输入（续接）' : association === 'candidate' ? '前序用户输入' : '对应用户输入','dc-label'),el('h3',requestText || '未找到对应用户输入'));
    heading.append(title,chip(association === 'candidate' ? '待确认关联' : association === 'recorded' ? requestAssociationLabel(request) : '归属未确认',association === 'recorded' ? 'blue' : 'attention')); card.append(heading);
    if (association === 'unknown') card.append(el('p',actionName(arr(group.items)[0] || {}),'dc-group-unknown-action'));
    if (!requestText && (request.reason || request.basis)) card.append(el('p',requestDiagnostic(request),'dc-group-association'));
    if (request.inputKind === 'continuation' && requestText) card.append(el('p','本轮保留继续或确认消息的原文；这句话本身不代表完整需求。','dc-group-association'));
    appendContextHint(card,request);
    const meta = el('div',null,'dc-result-meta');
    const devices = arr(group.devices).map(device => typeof device === 'string' ? device : device.name || device.id).filter(Boolean), applications = arr(group.applications).map(appName);
    meta.append(el('span','上报机器：' + (devices.join('、') || '设备名称未采集'),'dc-machine'),el('span',applications.join('、') || 'Agent 未确认'),el('span',date(group.firstSeen) + (group.lastSeen !== group.firstSeen ? ' — ' + date(group.lastSeen) : ''))); card.append(meta);
    const counts = el('div',null,'dc-group-summary'), summaryItems = arr(group.items), activityCounts = new Map();
    for (const item of summaryItems) {const activity = activityFor(item); if (!activity) continue; const key = activityLabel(activity) + ' · ' + activity.name; activityCounts.set(key,(activityCounts.get(key) || 0)+1);}
    for (const [label,count] of activityCounts) counts.append(chip(label,'blue'),el('span',summaryItems.length === Number(group.recordCount) ? count + ' 次' : '见已保留调用摘要','dc-group-action-count'));
    const toolCounts = arr(group.toolCallCounts).filter(entry => text(entry.name) && Number(entry.count)>0), allMembers = summaryItems.length === Number(group.recordCount);
    if (!toolCounts.length && allMembers) {const tools = new Map(); for (const item of summaryItems) if (item.kind === 'tool_call' && text(item.tool || item.function)) {const name = item.tool || item.function; tools.set(name,(tools.get(name) || 0)+1);} for (const [name,count] of tools) toolCounts.push({name,count});}
    if (toolCounts.length) for (const entry of toolCounts.slice(0,3)) counts.append(chip('工具 · ' + entry.name,'blue'),el('span',entry.count + ' 次调用','dc-group-action-count'));
    else if (!activityCounts.size) {const names = [...new Set(summaryItems.map(item => text(item.tool || item.function)).filter(Boolean))]; counts.append(el('span',names.length ? '匹配工具：' + names.slice(0,3).join('、') : [...new Set(summaryItems.map(actionName))].slice(0,3).join(' · '),'dc-group-action-count'));}
    counts.append(el('strong',Number(group.recordCount || 0) + ' 条匹配记录','dc-group-record-count')); card.append(counts);
    if (association === 'candidate') card.append(el('p','前序记录找到这条用户输入；这些调用是否属于同一任务仍待核对。','dc-group-association'));
    else if (association === 'unknown') card.append(el('p','保留为独立记录，未与其他需求合并。','dc-group-association'));
    if (requestText.length > 200) {const original = el('details',null,'dc-inline-details'); original.append(el('summary','展开用户输入片段'),el('p',requestText,'dc-value-text')); card.append(original);}
    const members = el('section',null,'dc-group-members'); members.id = 'dc-group-members-' + groupStates.size; members.hidden = true; members.setAttribute('aria-label','这项需求线索的匹配记录');
    const position = el('p',null,'dc-group-position'), list = el('div'), more = el('button','加载更多匹配记录'), memberError = el('p',null,'dc-error'); more.type = 'button'; memberError.hidden = true; memberError.setAttribute('role','alert'); members.append(position,list,memberError,more);
    const state = {group,items:[...summaryItems],params:{...lastSearchParams},page:1,hasMore:!!group.hasMore,loading:false,failed:false,list,position,more,error:memberError,controller:null}; groupStates.set(group.id,state); more.addEventListener('click',() => loadGroupMembers(state));
    const bottom = el('div',null,'dc-group-bottom');
    if (association !== 'unknown' && request.recordId) {const source = el('button','查看用户输入原文 →','dc-group-source'); source.type = 'button'; source.setAttribute('aria-label','查看这条用户输入的原始记录'); source.addEventListener('click',() => openDetail(request.recordId,source)); bottom.append(source);}
    const expand = el('button','展开 ' + group.recordCount + ' 条匹配记录'); expand.type = 'button'; expand.setAttribute('aria-expanded','false'); expand.setAttribute('aria-controls',members.id); expand.addEventListener('click',() => {members.hidden = !members.hidden; expand.setAttribute('aria-expanded',String(!members.hidden)); expand.textContent = members.hidden ? '展开 ' + group.recordCount + ' 条匹配记录' : '收起匹配记录';}); bottom.append(expand); card.append(bottom,members); renderGroupMembers(state); return card;
  }
  function renderSearch(data, query) {
    const grouped = resultView() === 'request' && Array.isArray(data.groups), rows = grouped ? data.groups : arr(data.items); page = Number(data.page || page); lastSearch = data;
    put('dc-count',grouped ? groupedCount(data) : Number(data.total || 0).toLocaleString('zh-CN') + ' 条匹配记录'); put('dc-coverage',coverageText(data.coverage,Number(grouped ? data.recordTotal : data.total || 0))); renderCoverageDetails(data.coverage);
    $('dc-results').replaceChildren(...(grouped ? rows.map(group => requestGroupCard(group,query)) : rows.map(item => {
      const summary = item.summary || {}, activity = activityFor(item), execution = executionFor(item), readableExecution = execution && !activity, card = el('article',null,'card dc-result' + (activity ? ' dc-capability-result' : '')); card.tabIndex = 0; card.setAttribute('aria-label','查看 ' + actionName(item) + ' 的完整信息');
      const heading = el('div',null,'dc-result-heading'), left = el('div',null,'dc-result-title'); left.append(el('span',date(item.timestamp),'dc-result-time'),el('h3',actionName(item))); heading.append(left,chip(activity ? activityLabel(activity) : item.kindLabel || kinds[item.kind] || '来源记录','blue')); card.append(heading);
      const destinations = arr(item.destinations).filter(value => text(value));
      const targets = destinations.length ? [...new Set(destinations)] : [text(summary.target || item.destination)].filter(Boolean);
      for (const target of targets) {const row = el('p',null,'dc-result-target'); row.append(el('span',item.addressBasis === 'text_mention' ? '正文提及' : item.addressBasis === 'captured_request_target' ? '请求目的地' : '参数目标','dc-field-name'),el('span',target)); card.append(row);}
      const meta = el('div',null,'dc-result-meta'); meta.append(el('span','上报机器：' + known(item.deviceName || item.sourceDeviceId || item.deviceId,'设备名称未采集'),'dc-machine'),el('span',appName(item.application)),el('span',collectorName(item.collector)),el('span','上传账号：' + known(item.ownerAccount,'未确认')),el('span',operator(item) === '操作人员未确认' ? operator(item) : '操作人员：' + operator(item))); card.append(meta);
      const request = el('div',null,'dc-card-request'), userRequest = ['user','user_message'].includes(item.kind) && text(item.userInput).trim() ? {text:item.userInput,recordId:item.id,association:'recorded'} : requestFor(item,activity); appendRequest(request,userRequest,true); card.append(request);
      if (readableExecution) card.append(executionView(item,{compact:true}));
      else {
        const result = activity ? activityReturnSummary(activity) : resultSummary(summary); if (result || (['tool_call','tool_result'].includes(item.kind) && summary.outcome?.status)) {const n = el('div',null,'dc-result-outcome'); if (activity) n.append(el('span',activity.returnKind === 'wrapper_result' ? '外层返回' : activity.returnKind === 'instructions' ? '说明返回' : '返回','dc-field-name')); else if (['tool_call','tool_result'].includes(item.kind) && summary.outcome?.status) n.append(chip(outcomeHeading(summary.outcome),outcomeClass(summary.outcome.status))); if (result) n.append(el('span',short(result,300))); if (summary.outcome?.sourceTruncated) n.append(chip('返回被截取','attention')); card.append(n);}
        if (!activity && summary.promptPreview) card.append(el('p',summary.promptPreview,'dc-result-prompt'));
        else if (!result && !['tool_call','tool_result'].includes(item.kind) && !(['user','user_message'].includes(item.kind) && text(item.userInput).trim()) && text(item.excerpt)) card.append(el('p',short(item.excerpt,200),'dc-result-preview'));
        appendFactItems(card,arr(activity ? activity.parameters : summary.parameters).filter(f => f.key !== 'prompt' && (!item.command || !['cmd','command'].includes(f.key)) && (f.key !== 'description' || f.value !== summary.action)).slice(0,4));
        if (execution && ['skill_read','skill_load'].includes(activity?.kind)) card.append(executionView(item,{compact:true,showReturns:false,showTechnical:false}));
      }
      const objects = el('div',null,'dc-result-objects'); for (const [label,value] of readableExecution ? [['工具',item.tool]] : [['工具',item.tool],['执行器',item.executor],['函数',item.function],['命令',activity ? '' : item.command]]) {if (!text(value) || (['执行器','函数'].includes(label) && value === item.tool) || (label === '函数' && value === item.executor)) continue; const field = el('span'); field.append(el('span',label,'dc-field-name'),el('span',short(value,label === '命令' ? 180 : 80))); objects.append(field);} if (objects.children.length) card.append(objects);
      const capabilities = capabilityList(item,3); if (capabilities.children.length) card.append(capabilities);
      const matches = arr(item.matchBasis), matchDetails = el('details',null,'dc-result-match-details'); matchDetails.append(el('summary','命中依据' + (matches.length ? ' · ' + [...new Set(matches.map(m => m.label || m.field))].join('、') : ''))); if (matches.length) matchDetails.append(...matches.slice(0,2).map(m => matchBlock(m,query))); else if (item.excerpt) matchDetails.append(matchBlock({label:'采集片段',excerpt:item.excerpt},query));
      matchDetails.addEventListener('click',e => e.stopPropagation()); matchDetails.addEventListener('keydown',e => e.stopPropagation()); if (matches.length || item.excerpt) card.append(matchDetails);
      const bottom = el('div',null,'dc-result-bottom'); bottom.append(el('span',item.destination ? addressLabels[item.addressBasis] || '地址依据待核对' : item.confirmation === 'inferred' ? '关联依据待核对' : '源记录已保留','muted')); const button = el('button','查看完整信息 →'); button.type = 'button'; button.addEventListener('click',e => {e.stopPropagation(); openDetail(item.id,button);}); bottom.append(button); card.append(bottom);
      card.addEventListener('click',e => {if (e.target !== card && /^(SUMMARY|DETAILS|A|BUTTON)$/.test(e.target.tagName || '')) return; openDetail(item.id,card);}); card.addEventListener('keydown',e => {if (['Enter',' '].includes(e.key) && e.target === card) {e.preventDefault(); openDetail(item.id,card);}}); return card;
    })));
    $('dc-empty').hidden = rows.length > 0; put('dc-empty','没有找到符合条件的记录。可以调整关键词、内容位置或查询范围。');
    $('dc-prev').disabled = page <= 1; $('dc-next').disabled = !data.hasMore;
    put('dc-page-position',rows.length ? '第 ' + page + ' 页 · 本页 ' + rows.length + (grouped ? ' 个分组' : ' 条') : '没有匹配记录'); renderFacets(data.facets);
  }
  async function search(resetPage = false) {
    if (mode !== 'search') return;
    if (resetPage) {page = 1; querySnapshot = '';}
    const generation = ++searchGeneration; searchController?.abort(); clearGroupStates(); lastSearchParams = null; searchController = new AbortController(); const controller = searchController; renderResultView();
    closeDetail(); clearError(); $('dc-results').replaceChildren(); $('dc-results').setAttribute('aria-busy','true'); $('dc-empty').hidden = true; $('dc-progress').hidden = false; put('dc-stage','正在查询平台已接收的记录'); put('dc-count','正在查询…'); put('dc-coverage',''); $('dc-coverage-details').hidden = true; put('dc-page-position',''); $('dc-prev').disabled = true; $('dc-next').disabled = true;
    let params;
    try {
      params = filterParams(); const signature = JSON.stringify({...params,page:undefined});
      if (signature !== querySignature) {querySnapshot = ''; page = 1; params.page = '1'; querySignature = signature;}
      if (querySnapshot) params.snapshot = querySnapshot;
      const data = await api('/api/data-center/search?' + new URLSearchParams(params),undefined,controller.signal);
      if (generation !== searchGeneration || mode !== 'search') return;
      if (typeof data.snapshot === 'string') querySnapshot = data.snapshot;
      lastSearchParams = {...params,...(querySnapshot ? {snapshot:querySnapshot} : {})};
      renderSearch(data,params.q);
    }
    catch (e) {if (generation !== searchGeneration || controller.signal.aborted) return; if (e.message.includes('快照')) {querySnapshot = ''; page = 1;} error(e.message); recover(e.message.includes('快照') ? '上次查询范围已失效，重试会从第一页重新检索。查询条件已保留。' : '未能完成搜索。查询条件已保留。'); put('dc-count','搜索未完成'); put('dc-coverage','');}
    finally {if (generation === searchGeneration) {$('dc-results').setAttribute('aria-busy','false'); $('dc-progress').hidden = true;}}
    saveDraft();
  }
  function facts(container, entries) {container.replaceChildren(...entries.flatMap(([label,value]) => [el('dt',label),el('dd',known(value))]));}
  function readable(value, emptyLabel = '未采集') {
    const container = el('div'); if (value == null || value === '') {container.append(el('p',emptyLabel,'dc-coverage')); return container;}
    if (typeof value === 'object' && !Array.isArray(value)) {const table = el('table',null,'dc-readable-table'), body = el('tbody'); Object.entries(value).forEach(([k,v]) => {const row = el('tr'), cell = el('td'), valueText = typeof v === 'string' && basisLabels[v] ? basisLabels[v] : typeof v === 'boolean' ? readableScalar(v) : formatted(v); if (valueText.length > 500 || (v && typeof v === 'object')) {const more = el('details',null,'dc-field-detail'); more.append(el('summary',Array.isArray(v) ? v.length + ' 个条目 · 展开内容' : v && typeof v === 'object' ? Object.keys(v).length + ' 个字段 · 展开内容' : short(valueText,180) + ' · 展开全文'),el('pre',valueText)); cell.append(more);} else cell.textContent = valueText; row.append(el('td',fieldLabels[k] ? fieldLabels[k] + '（' + k + '）' : k),cell); body.append(row);}); table.append(body); container.append(table);}
    else container.append(el('p',formatted(value),'dc-value-text'));
    return container;
  }
  function relationLabel(value) {return {tool_call_result:'调用 → 对应返回',source_parent:'原记录父子关系',native_user_round:'用户输入 → 对应记录轮次',preceding_user_candidate:'前序用户提问候选',input_contains:'材料出现在输入中',tool_result_input:'工具返回 → 后续模型输入'}[value] || value || '记录关联';}
  function outcomeContent(outcome, fallback) {
    const container = el('div',null,'dc-readable-outcome');
    if (!outcome) {container.append(readable(fallback,'未采集到可唯一对应的返回，请核对关联缺口。')); return container;}
    if (outcome.status || outcome.label) container.append(chip(outcomeHeading(outcome),outcomeClass(outcome.status)));
    if (arr(outcome.facts).length) {const dl = el('dl',null,'dc-outcome-facts'); facts(dl,outcome.facts.map(f => [f.label || f.key || '返回字段',displayFactValue(f)])); container.append(dl);}
    if (arr(outcome.outputs).length) {const outputs = el('section',null,'dc-output-list'); outputs.append(el('h5','返回的文件')); for (const o of outcome.outputs) {const n = el('p'), path = text(o.path || o.url); n.append(el('strong',o.label && o.label !== path ? o.label : path.split(/[\\/]/).pop() || '输出文件'),el('span',path || '未提供路径')); outputs.append(n);} container.append(outputs);}
    if (outcome.exitCode != null) {const dl = el('dl',null,'dc-outcome-facts'); facts(dl,[['退出码',String(outcome.exitCode)]]); container.append(dl);}
    if (text(outcome.stderr).trim() && !/^\((?:empty|none)\)$/i.test(text(outcome.stderr).trim())) {const section = el('section',null,'dc-output-text'); section.append(el('h5','错误输出'),el('p',outcome.stderr,'dc-value-text')); container.append(section);}
    if (outcome.stdout && !arr(outcome.facts).length && (typeof outcome.parsed !== 'object' || outcome.parsed == null)) {const section = el('section',null,'dc-output-text'); section.append(el('h5','标准输出'),el('p',outcome.stdout,'dc-value-text')); container.append(section);}
    if (outcome.error || outcome.message) {const section = el('section',null,'dc-output-text'); section.append(el('h5',outcome.error ? '错误信息' : '返回说明'),el('p',formatted(outcome.error || outcome.message),'dc-value-text')); container.append(section);}
    if (!arr(outcome.facts).length && !arr(outcome.outputs).length) {
      const parsed = outcome.parsed;
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {const entries = Object.entries(parsed).filter(([key]) => !['status','type','error','message','exitCode','exit_code','stderr'].includes(key)); if (entries.length) container.append(readable(Object.fromEntries(entries.slice(0,8))));}
      else if (!outcome.stdout && typeof parsed === 'string' && parsed.trim()) container.append(el('p',short(parsed,400),'dc-value-text'));
    }
    const daily = outcome.parsed?.weather || outcome.parsed?.daily;
    if (Array.isArray(daily) && daily.length) {const table = el('table',null,'dc-weather-table'), head = el('thead'), hr = el('tr'); for (const x of ['日期','最低温','最高温','日间信息']) hr.append(el('th',x)); head.append(hr); const body = el('tbody'); for (const day of daily) {const row = el('tr'), description = arr(day.hourly).map(x => arr(x.lang_zh)[0]?.value || arr(x.weatherDesc)[0]?.value).filter(Boolean); for (const x of [day.date || '',day.mintempC != null ? day.mintempC + '℃' : '未采集',day.maxtempC != null ? day.maxtempC + '℃' : '未采集',[...new Set(description)].map(value => displayFactValue({label:'天气',value})).join('、') || '见完整返回']) row.append(el('td',x)); body.append(row);} table.append(head,body); container.append(el('h5','返回中的逐日天气'),table);}
    const raw = el('details',null,'dc-inline-details'); raw.append(el('summary','展开已采集的原始工具返回'),readable(fallback ?? outcome.parsed)); container.append(raw);
    if (outcome.sourceTruncated) container.append(el('p','返回内容被截取或未形成完整结构；这里展示已取得的字段，原始返回可展开核对。','dc-coverage'));
    return container;
  }
  function activityArguments(activity, item) {
    const container = el('div',null,'dc-activity-arguments'), calls = arr(activity.calls), wrapped = activityWrapped(activity,item);
    if (calls.length) {
      for (const [index,call] of calls.entries()) {
        const row = el('section',null,'dc-activity-call'), name = known(call.serviceName || call.name,activity.name), callTitle = call.type === 'skill' ? (call.evidence === 'loaded' ? '加载 ' : '读取 ') + name + (call.evidence === 'loaded' ? '' : ' 说明') : '调用 ' + name + (call.method ? ' · ' + call.method : '');
        if (calls.length > 1) row.append(el('h5',(index + 1) + '. ' + callTitle));
        const params = call.arguments ?? (arr(call.parameters).length ? Object.fromEntries(call.parameters.map(p => [p.key || p.label,p.value])) : call.path ? {path:call.path} : !wrapped && calls.length === 1 && arr(activity.parameters).length ? Object.fromEntries(activity.parameters.map(p => [p.key || p.label,p.value])) : null);
        row.append(readable(params,'本项调用参数未单独采集。'));
        const provenance = [];
        if (wrapped) provenance.push('从外层调用源码识别');
        else if (call.evidence) provenance.push(capabilityEvidence(call));
        if (call.sourceOffset != null) provenance.push('源码位置：' + (typeof call.sourceOffset === 'object' ? formatted(call.sourceOffset) : call.sourceOffset));
        if (call.outerCallId) provenance.push('外层调用标识：' + call.outerCallId);
        if (provenance.length) row.append(el('p',provenance.join(' · '),'dc-coverage'));
        const evidence = call.provenance || call.basis; if (evidence) {const more = el('details',null,'dc-inline-details'); more.append(el('summary','本项识别依据'),readable(evidence)); row.append(more);}
        container.append(row);
      }
    } else if (arr(activity.parameters).length) container.append(readable(Object.fromEntries(activity.parameters.map(p => [p.key || p.label,p.value]))));
    else if (!wrapped) container.append(readable(item.arguments));
    if (wrapped) {const outer = el('details',null,'dc-inline-details dc-outer-call'); outer.append(el('summary','展开外层 ' + known(item.tool || item.function,'工具') + ' 调用参数'),readable(item.arguments)); container.append(outer);}
    return container;
  }
  function activityReturnContent(activity, item, outcome) {
    const container = el('div',null,'dc-activity-return'); container.append(el('p',activityReturnSummary(activity),'dc-value-text'));
    if (activity.kind === 'skill_load' || activity.kind === 'skill_read') container.append(el('p','这条记录说明加载或读取了 Skill 文档；实际使用它完成的动作需要后续调用证据。','dc-coverage'));
    if (activity.returnKind === 'tool_result') container.append(outcomeContent(outcome,item.result));
    else if (activity.returnKind !== 'missing' && item.result != null) {
      if (activity.returnKind === 'wrapper_result') container.append(el('p','以下返回属于外层 ' + known(item.tool || item.function,'工具') + ' 调用。尚未为各项子调用分别配对返回。','dc-coverage'));
      const raw = el('details',null,'dc-inline-details'); raw.append(el('summary',activity.returnKind === 'wrapper_result' ? '展开外层工具的完整返回' : '展开完整 Skill 说明返回'),readable(item.result)); container.append(raw);
    }
    if (outcome?.sourceTruncated) container.append(el('p','源返回被截取；可核对已采集原文。','dc-coverage'));
    return container;
  }
  function processRecord(record, selectedId, association, relationBasis) {
    const step = el('article',null,'dc-process-record' + (record.id === selectedId ? ' selected' : '')), heading = el('div',null,'dc-process-heading');
    const kindLabel = record.kindLabel || kinds[record.kind] || '来源记录', action = actionName(record); heading.append(chip(kindLabel,record.id === selectedId ? 'blue' : '')); if (![kindLabel,kinds[record.kind]].includes(action)) heading.append(el('strong',action)); heading.append(el('time',date(record.timestamp))); step.append(heading);
    if (record.id === selectedId) step.append(el('span','当前记录','dc-process-association'));
    else if (association === 'candidate' || association === 'same_session_neighbor') step.append(el('span',association === 'candidate' ? '候选关联 · 需核对归属' : '同一源会话邻近记录 · 归属未确认','dc-process-association attention'));
    else if (association === 'recorded' || relationBasis) step.append(el('span',basis(relationBasis) || '原记录有直接关联','dc-process-association'));
    const activity = activityFor(record), preview = text(activity ? activityReturnSummary(activity) : ['user','user_message'].includes(record.kind) && record.userInput ? record.userInput : ['tool_call','tool_result'].includes(record.kind) ? (resultSummary(record.summary) || record.summary?.promptPreview || record.command || record.excerpt || record.presentation?.bodyText) : (record.presentation?.bodyText || record.excerpt || resultSummary(record.summary)));
    if (preview) step.append(el('p',short(preview,300),'dc-process-preview'));
    const targets = arr(record.destinations).length ? record.destinations : [record.destination].filter(Boolean); for (const target of targets) step.append(el('p',target,'dc-process-target'));
    if (preview.length > 300 || record.kind === 'reasoning') {const more = el('details',null,'dc-inline-details'); more.append(el('summary',record.kind === 'reasoning' ? '查看已记录思路原文' : '展开此段已采内容'),el('p',preview || formatted(record.content),'dc-value-text')); step.append(more);}
    if (record.id && record.id !== selectedId) {const button = el('button','查看这条记录的完整信息 →'); button.type = 'button'; button.addEventListener('click',() => openDetail(record.id,button)); step.append(button);} return step;
  }
  function chronological(records) {return records.map((record,index) => ({record,index})).sort((a,b) => {const av = Number(a.record.timestamp), bv = Number(b.record.timestamp); return Number.isFinite(av) && Number.isFinite(bv) ? av-bv || a.index-b.index : a.index-b.index;}).map(x => x.record);}
  function renderDetail(item, searchMatches = []) {
    detailItem = item; const summary = item.summary || {}, presentation = item.presentation || {}, portrait = item.portrait || {}, activity = activityFor(item), execution = executionFor(item), readableExecution = execution && !activity;
    put('dc-detail-title',actionName(item)); put('dc-detail-kind',collectorName(item.collector) + ' · ' + appName(item.application) + ' · ' + (item.kindLabel || kinds[item.kind] || '来源记录'));
    put('dc-detail-confirmation',item.confirmation === 'inferred' ? '包含推定关联' : '来源记录已保留');
    put('dc-detail-summary',activity ? activityReturnSummary(activity) : readableExecution ? execution.operations.map(operation => [operation.action,operation.objectSummary].filter(Boolean).join(' → ')).join('；') || execution.outer?.summary || '已保留外层调用返回，内部操作尚未识别。' : resultSummary(summary) || observedSummary(item));
    $('dc-detail-outcome').hidden = Boolean(activity || readableExecution) || !summary.outcome?.status || !['tool_call','tool_result'].includes(item.kind); $('dc-detail-outcome').replaceChildren(); if (!activity && !readableExecution && summary.outcome?.status) $('dc-detail-outcome').append(chip(outcomeHeading(summary.outcome),outcomeClass(summary.outcome.status)),el('span','状态来自工具返回；产物是否符合需求需另行核验。'));
    facts($('dc-detail-facts'),[['上报机器',item.deviceName || item.deviceId],['Agent',appName(item.application)],['采集器',collectorName(item.collector)],['记录时间',date(item.timestamp) + '（北京时间）'],['平台接收',date(item.receivedAt)],['上传账号',item.ownerAccount],['操作人员',operator(item)],['涉及地址',arr(item.destinations).length ? item.destinations.join('\n') : item.destination || '未采集'],['地址依据',addressLabels[item.addressBasis] || '未采集']]);
    const capabilities = capabilityList(item); $('dc-detail-capabilities').replaceChildren(); $('dc-detail-capabilities').hidden = !capabilities.children.length; if (capabilities.children.length) {$('dc-detail-capabilities').append(el('h4','识别到的 Skill / MCP'),capabilities); for (const c of arr(item.capabilities)) {if (c.basis || c.path) $('dc-detail-capabilities').append(el('p',(c.name || '') + ' · ' + [c.basis,c.path].filter(Boolean).join(' · '),'dc-coverage'));} $('dc-detail-capabilities').append(el('p','Skill 的说明读取与明确加载分别展示；MCP 调用保留直接调用或命令包装的识别依据。','dc-coverage'));}
    const matches = arr(item.matchBasis).length ? item.matchBasis : searchMatches.map(m => ({...m,label:'本次搜索命中 · ' + (m.label || m.field || '原文')}));
    $('dc-detail-matches').replaceChildren(...matches.map(m => matchBlock(m,$('dc-query').value.trim()))); $('dc-detail-match-details').hidden = !matches.length;
    const task = portrait.userRequest || item.taskContext || {}; $('dc-task-context').replaceChildren();
    if (activity || item.request) appendRequest($('dc-task-context'),requestFor(item,activity));
    else {$('dc-task-context').append(el('p',task.userInput || '尚未关联到可确认的用户需求。','dc-task-request'),el('p',task.association === 'recorded' ? '用户输入有原记录关联。' : task.association === 'candidate' ? '候选需求 · 根据前序记录找到，尚未确认属于同一任务。' : '任务归属尚未确认。','dc-task-association')); if (task.basis) $('dc-task-context').append(el('p',basis(task.basis),'dc-coverage')); if (task.limitation) $('dc-task-context').append(el('p',task.limitation,'dc-coverage'));}
    const related = arr(item.related), lookup = new Map([item,...related].map(r => [r.id || r.recordId,r])); for (const record of [item,...related]) if (record.recordId) lookup.set(record.recordId,record);
    const relations = orderedRelations(arr(item.relations),lookup);
    $('dc-relations').replaceChildren(...relations.map(r => {const row = el('div',null,'dc-relation'), left = lookup.get(r.from), right = lookup.get(r.to); row.append(el('span',left ? title(left) : '关联记录'),el('span',relationLabel(r.relation),'dc-relation-label'),el('span',right ? title(right) : '关联记录'),chip(r.relation === 'preceding_user_candidate' ? '候选，待确认' : basis(r.basis) || '原记录关联',r.relation === 'preceding_user_candidate' ? 'attention' : '')); return row;}));
    if (!relations.length) $('dc-relations').append(el('p','尚无可核对的记录连接；不按相近时间或同一 Session 补画执行链。','dc-coverage'));
    const steps = arr(portrait.steps).length ? portrait.steps : chronological([item,...related]).map(r => {const edge = relations.find(x => [x.from,x.to].includes(r.id) || [x.from,x.to].includes(r.recordId)); return {...r,association:r.id === item.id ? 'selected' : edge?.relation === 'preceding_user_candidate' ? 'candidate' : edge ? 'recorded' : 'candidate',relationBasis:edge?.basis};});
    const followups = activity ? steps.filter(r => r.id !== item.id && !['user','user_message'].includes(r.kind) && r.association === 'recorded' && Number(r.timestamp) >= Number(item.timestamp) && !relations.some(edge => edge.relation === 'tool_call_result' && [item.id,item.recordId].includes(edge.from) && [r.id,r.recordId].includes(edge.to))) : steps;
    $('dc-related').replaceChildren(...followups.map(r => processRecord(r,item.id,r.association,r.relationBasis))); if (activity && !followups.length) $('dc-related').append(el('p','尚未找到可确认归属的后续动作。可在关联依据与附近记录中继续核对。','dc-coverage')); put('dc-process-heading',activity ? '后续已关联的动作' : '这条记录的前后过程'); put('dc-process-count',followups.length + ' 条已采记录'); put('dc-process-note',activity ? '这里只展示时间在当前调用之后、且原记录明确关联的动作。Skill 说明返回本身不代表已执行浏览、开发或其他操作。' : '按记录时间组织。原始标识关联与候选关联分别标注，不将整个源会话视为一项任务。');
    const neighbors = arr(portrait.neighbors); $('dc-neighbor-details').hidden = !neighbors.length; put('dc-neighbor-title','同一会话附近的其他记录 · ' + neighbors.length + ' 条'); $('dc-neighbors').replaceChildren(...neighbors.map(r => processRecord(r,item.id,'same_session_neighbor')));
    const toolVisible = Boolean(item.tool || item.function || item.command || item.arguments != null || item.result != null || ['tool_call','tool_result'].includes(item.kind)); $('dc-tool-section').hidden = !toolVisible;
    $('dc-execution-detail').replaceChildren(); $('dc-execution-detail').hidden = !readableExecution; if (readableExecution) $('dc-execution-detail').append(executionView(item)); $('dc-tool-grid').hidden = Boolean(readableExecution); $('dc-tool-facts').hidden = Boolean(readableExecution);
    facts($('dc-tool-facts'),[['工具',item.tool],['函数',item.function],['执行命令',item.command],['执行器',item.executor || '未单独采集'],['实际目标',arr(item.destinations).length ? item.destinations.join('\n') : item.destination],['调用依据',item.confirmation === 'inferred' ? '待核对' : '来源记录']].filter(([label]) => label !== '函数' || !item.tool || item.function !== item.tool));
    const parameters = arr(presentation.parameters), prompt = text(presentation.prompt || item.arguments?.params?.prompt || item.arguments?.prompt); $('dc-prompt-section').hidden = Boolean(readableExecution) || !prompt; put('dc-prompt',prompt);
    const parameterObject = Object.fromEntries(parameters.filter(f => f.key !== 'prompt' && (f.key !== 'description' || f.value !== summary.action)).map(f => [f.key || f.label,f.value]));
    put('dc-arguments-title',activity ? '能力调用的参数与目标' : '调用参数与实际目标'); put('dc-return-title',activity?.returnKind === 'wrapper_result' ? '外层工具的返回' : activity?.returnKind === 'instructions' ? '取得的 Skill 说明' : '工具返回的内容');
    $('dc-arguments').replaceChildren(activity ? activityArguments(activity,item) : readable(parameters.length ? parameterObject : item.arguments)); if (execution && ['skill_read','skill_load'].includes(activity?.kind)) $('dc-arguments').append(executionView(item,{showReturns:false}));
    const returned = presentation.result; put('dc-tool-status',readableExecution ? execution.outer?.statusLabel || '外层调用记录' : activity ? activity.returnKind === 'instructions' ? '已取得说明' : activity.returnKind === 'wrapper_result' ? '外层返回' : activity.returnKind === 'missing' ? '返回待核对' : outcomeHeading(returned) : returned ? outcomeHeading(returned) : '返回待核对'); $('dc-tool-status').className = 'dc-chip ' + (readableExecution ? outcomeClass(execution.outer?.status) : activity ? 'blue' : outcomeClass(returned?.status)); $('dc-return').replaceChildren(activity ? activityReturnContent(activity,item,returned) : outcomeContent(returned,item.result));
    const contextItems = arr(item.contextItems); $('dc-context-section').hidden = !contextItems.length;
    $('dc-context-items').replaceChildren(...contextItems.map(c => {const row = el('section',null,'dc-content-item'), heading = el('div'); heading.append(el('strong',c.name || c.category || c.label || '输入内容'),chip(c.source || c.role || '来源依据待核对')); const body = text(c.rawContent ?? c.content ?? c.text); row.append(heading,el('p',short(body,350))); if (c.classificationBasis || c.basis) row.append(el('p',c.classificationBasis || c.basis,'dc-coverage')); const raw = el('details'); raw.append(el('summary','查看完整输入块'),el('pre',body || formatted(c))); row.append(raw); return row;}));
    const coverage = portrait.coverage || {}, gaps = [...arr(item.gaps)]; if (coverage.relatedCapped) gaps.push('本次画像最多展开 ' + (coverage.relatedLimit || 24) + ' 条关联记录，仍有相关记录未在此详情中展开。'); if (coverage.truncatedRecords) gaps.push('画像中的 ' + coverage.truncatedRecords + ' 条关联记录按展示长度截取；可进入各条记录核对完整原文。'); $('dc-record-gaps').replaceChildren(...gaps.map(g => el('li',typeof g === 'string' ? g : formatted(g))));
    if (!gaps.length) $('dc-record-gaps').append(el('li','当前详情按已采集的记录组织；尚不能确认整项任务或全部模型交互已完整采集。'));
    $('dc-source-evidence').replaceChildren(readable(item.sourceEvidence,'未提供独立采集完整性证据。'));
    put('dc-record-boundary',item.kind === 'reasoning' ? '已记录思路仅代表应用日志保留的内容，不代表模型全部思考过程。' : item.addressBasis === 'text_mention' ? '地址出现在文字中，不能据此认定发生实际访问或资料外发。' : item.addressBasis === 'tool_argument_target' ? '命令或工具参数记载访问对象；执行成功和实际网络请求需要对应返回或网络证据。' : '记录的出现位置、实际发送、接收成功和风险成立需要分别核对。');
    put('dc-content',formatted(item.content ?? item.raw ?? '未采集')); put('dc-raw',formatted({sourceRecord:item.raw ?? null,auxiliary:{recordId:item.recordId,sessionId:item.sessionId,sourceDeviceId:item.sourceDeviceId,relations:item.relations,taskContext:item.taskContext,sourceEvidence:item.sourceEvidence,coverage:portrait.coverage}})); $('dc-raw-link').href = originalLink(item).href;
    put('dc-record-id','原始记录标识：' + (item.recordId || item.id)); $('dc-detail-body').setAttribute('aria-busy','false');
    for (const section of Array.from($('dc-detail-navigation').children)) {const target = $(section.dataset.section); section.hidden = !target || target.hidden; section.classList.toggle('active',section.dataset.section === 'dc-overview-section');}
  }
  async function openDetail(id, trigger, backwards = false) {
    if (!id) return; if ($('dc-detail').hidden) detailHistory = []; else if (!backwards && detailId && detailId !== id) detailHistory.push(detailId); $('dc-detail-back').hidden = !detailHistory.length;
    const generation = ++detailGeneration; detailController?.abort(); detailController = new AbortController(); const controller = detailController; detailId = id; detailItem = null; if ($('dc-detail').hidden || !detailTrigger) detailTrigger = trigger || detailTrigger;
    $('dc-detail').hidden = false; $('dc-detail-backdrop').hidden = false; document.body.style.overflow = 'hidden'; $('dc-close-detail').focus(); $('dc-detail-body').scrollTop = 0; $('dc-detail-error').hidden = true; $('dc-detail-body').setAttribute('aria-busy','true'); $('dc-detail-capabilities').hidden = true; $('dc-detail-capabilities').replaceChildren(); put('dc-detail-title','正在读取记录'); put('dc-detail-kind',''); put('dc-detail-confirmation',''); put('dc-detail-summary','正在读取已保存的原文与关联依据…');
    for (const key of ['dc-detail-facts','dc-detail-matches','dc-task-context','dc-relations','dc-related','dc-neighbors','dc-source-evidence','dc-record-gaps','dc-detail-outcome','dc-arguments','dc-return','dc-execution-detail']) $(key).replaceChildren();
    for (const key of ['dc-content','dc-raw','dc-record-boundary','dc-record-id','dc-process-count','dc-process-note','dc-prompt']) put(key,'');
    for (const key of ['dc-detail-match-details','dc-relation-details','dc-neighbor-details','dc-source-details','dc-content-details','dc-raw-details']) $(key).open = false;
    for (const key of ['dc-tool-section','dc-context-section','dc-prompt-section','dc-neighbor-details','dc-detail-outcome','dc-execution-detail']) $(key).hidden = true; $('dc-tool-grid').hidden = false; $('dc-tool-facts').hidden = false; $('dc-raw-link').hidden = true;
    const searchMatches = [...arr(lastSearch?.items),...Array.from(groupStates.values()).flatMap(state => state.items)].find(item => item.id === id)?.matchBasis || [];
    try {const data = await api('/api/data-center/record?' + new URLSearchParams({id}),undefined,controller.signal); if (generation !== detailGeneration || controller.signal.aborted) return; renderDetail(data.item || data,searchMatches); $('dc-raw-link').hidden = false;}
    catch (e) {if (generation !== detailGeneration || controller.signal.aborted) return; put('dc-detail-error-text',e.message); $('dc-detail-error').hidden = false; put('dc-detail-summary','未能读取此记录，不沿用上一条内容。'); $('dc-detail-body').setAttribute('aria-busy','false');}
  }
  function closeDetail() {++detailGeneration; detailController?.abort(); detailController = null; detailId = ''; detailItem = null; detailHistory = []; $('dc-detail').hidden = true; $('dc-detail-backdrop').hidden = true; document.body.style.overflow = ''; detailTrigger?.focus(); detailTrigger = null;}
  function stopQuestion() {if (questionRun) {clearTimeout(questionRun.pollTimer); clearTimeout(questionRun.deadlineTimer); questionRun.controller.abort();} questionRun = null; ++questionGeneration;}
  function current(run) {return questionRun === run && run.generation === questionGeneration && mode === 'investigate';}
  function finishQuestion(run) {if (!current(run)) return; clearTimeout(run.pollTimer); clearTimeout(run.deadlineTimer); questionRun = null; $('dc-progress').hidden = true; put('dc-submit','发起调查');}
  function failQuestion(run, message) {if (!current(run)) return; finishQuestion(run); error(message); recover(pendingQuestion?.id ? '问题与任务已保留，可以重新查看后台结果。' : '调查问题已保留，可以重试。','question');}
  function startQuestion(question, device, id = '') {
    stopQuestion(); clearError(); closeDetail(); $('dc-investigation-result').hidden = true; $('dc-progress').hidden = false; put('dc-stage',id ? '正在读取此前调查的结果' : '问题已保留，正在检索相关记录'); put('dc-submit','继续提问');
    const run = {generation:questionGeneration,question,device,id,controller:new AbortController(),pollTimer:null,deadlineTimer:null}; questionRun = run; lastQuestion = {question,device};
    run.deadlineTimer = setTimeout(() => failQuestion(run,run.id ? '等待超过 5 分钟。后台可能仍在处理，可以重新查看结果。' : '尚未取得调查编号，请稍后重试。'),300000); return run;
  }
  function answerEvidenceLink(fragment, device) {return originalLink(fragment,'查看完整原文 ↗',device);}
  function renderQuestion(result, run) {
    if (!text(result?.answer?.text)) throw Error('平台未返回可读的调查回答，请重试。');
    const deviceName = $('dc-device').selectedOptions?.[0]?.textContent || run.device;
    put('dc-asked','你的问题：' + run.question); put('dc-answer',result.answer.text); put('dc-answer-coverage','调查范围：' + deviceName + ' · ' + (coverageText(result.coverage) || '平台未提供本次检索覆盖数字'));
    $('dc-observations').replaceChildren(...arr(result.observations).map(o => {const n = el('article',null,'dc-observation'); n.append(el('strong',o.title || o.fact || '观察记录')); for (const key of ['fact','basis','impact']) if (o[key] && o[key] !== o.title) n.append(el('p',typeof o[key] === 'string' ? o[key] : formatted(o[key]))); return n;})); $('dc-observations').hidden = !arr(result.observations).length;
    const evidence = arr(result.evidence), refs = new Map();
    $('dc-answer-evidence-list').replaceChildren(...evidence.map((fragment,index) => {const n = el('article',null,'dc-answer-evidence-item'); n.id = 'dc-answer-fragment-' + index; n.append(chip(collectorName(fragment.collector) + ' · ' + (kinds[fragment.kind] || fragment.kind || '来源记录'),'blue'),el('p',fragment.text || '此片段没有可读正文'),answerEvidenceLink(fragment,run.device)); if (fragment.destination) n.append(el('p',fragment.destination + ' · ' + (addressLabels[fragment.addressBasis] || '地址依据待核对'),'dc-coverage')); if (fragment.truncated) n.append(el('p','回答使用了原文片段，可查看完整已采集记录。','dc-coverage')); if (fragment.ref) refs.set(fragment.ref,n); return n;}));
    $('dc-answer-refs').replaceChildren(...arr(result.answer.evidenceRefs).filter(ref => refs.has(ref)).map(ref => {const button = el('button',ref + ' · 核对依据'); button.type = 'button'; button.addEventListener('click',() => {$('dc-answer-evidence').open = true; refs.get(ref).scrollIntoView({block:'nearest'});}); return button;}));
    put('dc-answer-evidence-title','核对原文依据 · ' + evidence.length + ' 个片段'); $('dc-answer-evidence').open = false; $('dc-answer-evidence').hidden = !evidence.length;
    const gaps = arr(result.gaps); $('dc-answer-gaps').hidden = !gaps.length; $('dc-answer-gaps').open = false; put('dc-answer-gaps-title','仍需核对的地方 · ' + gaps.length + ' 项'); $('dc-answer-gaps-list').replaceChildren(...gaps.map(g => el('li',typeof g === 'string' ? g : formatted(g))));
    $('dc-candidates').replaceChildren(...arr(result.candidates).map(candidate => {const detail = el('details',null,'dc-candidate'), summary = el('summary'); summary.append(el('strong',candidate.title || '候选需求'),chip(candidate.status === 'ambiguous' ? '关联待确认' : '根据内容关联','attention')); detail.append(summary); if (candidate.reason) detail.append(el('p',candidate.reason)); if (arr(candidate.requirements).length) {detail.append(el('h4','调查背景：用户如何提出和修改要求')); const list = el('ol'); for (const r of candidate.requirements) {const li = el('li',r.text || '用户提问未采集'); if (r.recordId) li.append(originalLink({recordId:r.recordId,collector:'sessionlens',deviceId:run.device})); list.append(li);} detail.append(list);} if (arr(candidate.executions).length) detail.append(el('p','相关执行记录 ' + candidate.executions.length + ' 条；请通过原文核对参数、返回和归属。')); return detail;}));
    $('dc-investigation-result').hidden = false;
  }
  async function receiveQuestion(job, run) {
    if (!current(run)) return;
    if (job.id) {run.id = job.id; savePending({id:job.id,device:run.device,question:run.question});}
    if (job.status === 'completed') {renderQuestion(job.result,run); previousQuestionId = run.id; previousDevice = run.device; savePending(null); finishQuestion(run); return;}
    if (job.status === 'failed') {savePending(null); failQuestion(run,job.error || '本次调查未完成，请重试。'); return;}
    if (!['running','queued','pending'].includes(job.status) || !run.id) throw Error('平台没有返回可继续查询的调查状态。');
    put('dc-stage',stageLabels[job.stage] || '正在核对已采集的行为与证据');
    run.pollTimer = setTimeout(async () => {if (!current(run)) return; try {await receiveQuestion(await api('/api/collection/questions/' + encodeURIComponent(run.id),undefined,run.controller.signal),run);} catch (e) {if (current(run)) failQuestion(run,e.message);}},2000);
  }
  async function investigate(retry = false) {
    const question = $('dc-query').value.trim(), device = $('dc-device').value;
    if (!question) {error('请输入你想调查的问题。'); return;}
    if (device === 'all' || !device) {error('请选择一台具体设备。当前自然语言调查按单设备核对证据。'); return;}
    if (!session.csrf) {error('自然语言调查需要登录。关键词查询仍可使用。'); return;}
    const saved = pendingQuestion?.device === device && pendingQuestion.question === question ? pendingQuestion : null;
    if (!saved && !$('dc-share-confirmed').checked) {error('请先确认本次相关片段可以发送到已配置的分析服务。'); return;}
    const run = startQuestion(question,device,saved?.id || '');
    try {const job = saved ? await api('/api/collection/questions/' + encodeURIComponent(saved.id),undefined,run.controller.signal) : await api('/api/collection/questions',{deviceId:device,question,scope:'global',perspective:'security',shareConfirmed:true,...(previousDevice === device && previousQuestionId ? {previousQuestionId} : {})},run.controller.signal); await receiveQuestion(job,run);}
    catch (e) {if (current(run)) failQuestion(run,e.message);}
  }
  renderSearchFields();
  $('dc-search-form').addEventListener('submit',e => {e.preventDefault(); if (composingQuery) return; closeCompletions(); mode === 'search' ? search(true) : investigate();});
  $('dc-add-filter').addEventListener('click',()=>{closeCompletions(); const open=$('dc-filter-panel').hidden; $('dc-filter-panel').hidden=!open; $('dc-add-filter').setAttribute('aria-expanded',String(open));});
  for (const view of ['request','record']) $('dc-view-' + view).addEventListener('click',() => {if (resultView() === view) return; resultViewOverride = view; saveDraft(); search(true);});
  $('dc-mode-search').addEventListener('click',() => selectMode('search')); $('dc-mode-investigate').addEventListener('click',() => selectMode('investigate'));
  $('dc-latest').addEventListener('click',() => {selectMode('search'); $('dc-query').value = ''; resultViewOverride = ''; saveDraft(); search(true);});
  $('dc-query').addEventListener('input',()=>{updateQueryDraft();showCompletions();});
  $('dc-query').addEventListener('blur',()=>closeCompletions());
  $('dc-query').addEventListener('compositionstart',()=>{composingQuery=true;closeCompletions();});
  $('dc-query').addEventListener('compositionend',()=>{composingQuery=false;updateQueryDraft();showCompletions();});
  for (const event of ['focus','click']) $('dc-query').addEventListener(event,()=>showCompletions());
  $('dc-query').addEventListener('keyup',e=>{if (['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) showCompletions();});
  $('dc-query').addEventListener('keydown',e=>{
    if (composingQuery || e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Escape') {if (!$('dc-completions').hidden) e.preventDefault();closeCompletions();return;}
    if (mode !== 'search' || e.shiftKey && ['Enter','Tab'].includes(e.key)) return;
    if ((e.ctrlKey || e.metaKey) && e.key === ' ') {e.preventDefault();showCompletions(true);return;}
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {e.preventDefault();closeCompletions();search(true);return;}
    if ($('dc-completions').hidden) return;
    if (['ArrowDown','ArrowUp'].includes(e.key)) {e.preventDefault();highlightCompletion(completionIndex < 0 ? (e.key === 'ArrowDown'?0:completionItems.length-1) : (completionIndex+(e.key === 'ArrowDown'?1:-1)+completionItems.length)%completionItems.length);}
    else if (e.key === 'Enter' || e.key === 'Tab') {e.preventDefault();chooseCompletion(completionIndex < 0 ? 0 : completionIndex);}
  });
  document.addEventListener('click',e=>{let node=e.target;while(node && node !== $('dc-query') && node !== $('dc-completions')) node=node.parentElement;if (!node) closeCompletions();});
  for (const key of ['object','device','application','collector','kind','location']) $('dc-' + key).addEventListener('change',() => {stopQuestion(); $('dc-progress').hidden = true; $('dc-investigation-result').hidden = true; $('dc-share-confirmed').checked = false; if (key === 'object') resultViewOverride = ''; if (key === 'device') {previousQuestionId = ''; previousDevice = '';} renderScope(); saveDraft(); if (mode === 'search') search(true);});
  $('dc-apply-time').addEventListener('click',() => {renderScope(); if (mode === 'search') search(true); else error('自然语言调查目前只按所选设备检索，时间筛选仅作用于关键词查询。');});
  $('dc-reset').addEventListener('click',() => {stopQuestion(); resultViewOverride = ''; $('dc-progress').hidden = true; $('dc-investigation-result').hidden = true; previousQuestionId = ''; previousDevice = ''; for (const key of ['object','device','application','collector','kind','location']) $('dc-' + key).value = 'all'; $('dc-after').value = ''; $('dc-before').value = ''; $('dc-share-confirmed').checked = false; renderScope(); saveDraft(); if (mode === 'search') search(true);});
  $('dc-prev').addEventListener('click',() => {if (page > 1) {page -= 1; search();}}); $('dc-next').addEventListener('click',() => {if (lastSearch?.hasMore) {page += 1; search();}});
  $('dc-cancel').addEventListener('click',() => {if (mode === 'search') {searchController?.abort(); clearGroupStates(); ++searchGeneration; $('dc-results').setAttribute('aria-busy','false'); put('dc-count','已停止等待'); recover('已停止本页等待，查询条件已保留。');} else {stopQuestion(); recover(pendingQuestion?.id ? '已停止本页等待。后台调查可能仍在处理，可以重新查看结果。' : '已停止本页等待。问题已保留。','question');} $('dc-progress').hidden = true;});
  $('dc-retry').addEventListener('click',() => retryMode === 'question' ? investigate(true) : search());
  $('dc-detail-back').addEventListener('click',() => {const id = detailHistory.pop(); if (id) openDetail(id,null,true);});
  for (const button of Array.from($('dc-detail-navigation').children)) button.addEventListener('click',() => {const section = $(button.dataset.section); if (section && !section.hidden) {for (const other of Array.from($('dc-detail-navigation').children)) other.classList.toggle('active',other === button); section.scrollIntoView({block:'start',behavior:typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});}});
  $('dc-close-detail').addEventListener('click',closeDetail); $('dc-detail-backdrop').addEventListener('click',closeDetail); $('dc-retry-detail').addEventListener('click',() => openDetail(detailId));
  document.addEventListener('keydown',e => {
    if ($('dc-detail').hidden) return;
    if (e.key === 'Escape') {closeDetail(); return;}
    if (e.key === 'Tab') {const nodes = [...$('dc-detail').querySelectorAll('button, a[href], summary, [tabindex="0"]')].filter(n => !n.hidden && !n.disabled && n.getClientRects().length); const first = nodes[0], last = nodes.at(-1); if (e.shiftKey && document.activeElement === first) {e.preventDefault(); last?.focus();} else if (!e.shiftKey && document.activeElement === last) {e.preventDefault(); first?.focus();}}
  });
  addEventListener('pagehide',() => {searchController?.abort(); clearGroupStates(); detailController?.abort(); stopQuestion();});
  (async () => {
    const params = new URLSearchParams(location.search); let saved;
    try {saved = JSON.parse(sessionStorage.getItem(draftKey) || 'null'); pendingQuestion = JSON.parse(sessionStorage.getItem(pendingKey) || 'null');} catch (_) {}
    if (saved?.query) $('dc-query').value = saved.query;
    if (saved?.filters) {for (const key of ['application','collector','kind','location']) if (saved.filters[key] && [...$('dc-'+key).children].some(n=>n.value === saved.filters[key])) $('dc-'+key).value=saved.filters[key]; for (const key of ['after','before']) if (typeof saved.filters[key] === 'string') $('dc-'+key).value=saved.filters[key];}
    if (saved?.object && [...$('dc-object').children].some(n => n.value === saved.object)) $('dc-object').value = saved.object;
    for (const key of ['object','collector','application','kind','location']) if (params.get(key) && [...$('dc-' + key).children].some(n => n.value === params.get(key))) $('dc-' + key).value = params.get(key);
    for (const key of ['after','before']) if (params.get(key)) {const d = new Date(params.get(key)); if (!Number.isNaN(d.getTime())) {$('dc-' + key).value = new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}}
    const device = params.get('device') || saved?.device; if (device && device !== 'all') {$('dc-device').append(option(device,device)); $('dc-device').value = device;}
    if (params.has('q')) $('dc-query').value = params.get('q');
    if (['request','record'].includes(params.get('group'))) resultViewOverride = params.get('group');
    else if ((!params.has('q') || params.get('q') === saved?.query) && ['request','record'].includes(saved?.resultView)) resultViewOverride = saved.resultView;
    if (params.get('request')) {const old = $('dc-old-reader') || document.querySelector('.dc-old-reader'); if (old) old.href = '/model-data/raw?' + params.toString();}
    const identity = (async () => {try {session = await api('/api/session');} catch (_) {} finally {identityReady = true; renderIdentity();}})();
    if (saved?.mode === 'investigate' && !params.has('q') && !params.has('object')) {selectMode('investigate'); if (pendingQuestion?.id) recover('上次调查的问题和编号已保留，可以重新查看结果。','question'); const scope = await api('/api/data-center/search?' + new URLSearchParams({q:'',device:'all',page:'1',pageSize:'1'})).catch(() => null); if (scope) renderFacets(scope.facets);}
    else await search(); renderScope(); await identity;
  })();
})();
