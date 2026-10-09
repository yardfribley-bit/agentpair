/* Live SessionLens insights. Collection receipts and completed analysis are separate states. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  const labels = {waiting:'等待分析', queued:'已排队', running:'正在分析', completed:'已完成分析', failed:'分析失败', stale:'新记录待补充'};
  const kinds = {user_message:'用户提问', assistant_message:'Agent 回复', message:'会话消息', reasoning:'已记录思路', tool_call:'工具调用', tool_result:'工具返回', turn_completed:'本轮结束', turn_context:'上下文记录', file_change:'文件变更', command_execution:'命令执行', mcp_execution:'MCP 执行'};
  const pageSize = 20;
  let items = [], session = {}, page = 0, selectedId = '', detail = null, detailRequest = 0;
  let activeTab = 'process', currentStep = 0, evidenceLimit = 20, playTimer = null;
  let listSignature = '', detailSignature = '', loading = false, consentingId = '', consentingRevision = '';
  let hasMore = false, nextOffset = 0, totalSessions = 0;
  let automation = [], automationOptions = '', automationSubmitting = false;
  let budgetSource = '';

  const value = (v, fallback = '') => v == null ? fallback : String(v);
  const sourceName = source => ({codex:'Codex', workbuddy:'WorkBuddy'}[String(source).toLowerCase()] || value(source, '来源未确认'));
  const stateOf = item => Object.hasOwn(labels, item?.state) ? item.state : 'waiting';
  const previousAnalysis = item => Boolean(item?.report && item.revision !== item.analyzedRevision);
  const titleOf = item => value(previousAnalysis(item) ? item?.title || item?.titleHint || item?.report?.title : item?.report?.title || item?.title || item?.titleHint).trim() || sourceName(item?.source) + ' 会话 ' + value(item?.sessionId).slice(0, 8);
  const canAnalyze = item => Boolean(session.csrf && item?.permissions?.canAnalyze);
  const records = item => Number.isFinite(Number(item?.eventCount)) ? Number(item.eventCount).toLocaleString('zh-CN') : '—';
  const asArray = x => Array.isArray(x) ? x : [];
  const excerpt = (text, length = 180) => value(text).length > length ? value(text).slice(0, length) + '…' : value(text);
  const evidenceText = record => record?.text ?? record?.excerpt ?? '';
  function timeValue(input) {const numeric = Number(input); return input !== null && input !== '' && Number.isFinite(numeric) ? (numeric < 1e11 ? numeric * 1000 : numeric) : new Date(input).getTime() || 0;}
  function dateParts(input) {
    if (input == null || input === '') return null;
    let timestamp = input;
    if (typeof input === 'number' || /^\d+(?:\.\d+)?$/.test(String(input))) {
      const numeric = Number(input);
      timestamp = numeric < 1e11 ? numeric * 1000 : numeric;
    }
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return null;
    return new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'}).formatToParts(date).reduce((a, part) => {a[part.type] = part.value; return a;}, {});
  }
  function dateText(input) {
    const p = dateParts(input);
    return p ? `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}` : '未记录';
  }
  function dateCell(input) {
    const node = el('td', null, 'insight-date'), p = dateParts(input);
    if (p) node.append(el('span', `${p.month}-${p.day}`), el('span', `${p.hour}:${p.minute}:${p.second}`));
    else node.textContent = '未记录';
    node.title = dateText(input);
    return node;
  }
  function badge(item) {return el('span', item?.eligible === false && stateOf(item) === 'waiting' ? '仅环境 / 授权记录' : labels[stateOf(item)], 'insight-status ' + stateOf(item));}
  function setText(id, text) {$(id).textContent = value(text);}
  function showError(error) {setText('insight-error-text', excerpt(error?.message || error || '读取失败，请重试。', 220)); $('insight-error').hidden = false;}
  function clearError() {$('insight-error').hidden = true; setText('insight-error-text', '');}
  async function api(path, payload) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(path, {method:payload ? 'POST' : 'GET', credentials:'same-origin', cache:'no-store', signal:controller.signal,
        headers:payload ? {'Content-Type':'application/json', 'X-CSRF-Token':session.csrf || ''} : {}, body:payload ? JSON.stringify(payload) : undefined});
      let result;
      try {result = await response.json();} catch (_) {throw Error('平台返回了无法读取的内容，请重试。');}
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {session = {}; renderIdentity(); updateAnalyzeAction();}
        throw Error(result.error || '读取失败（HTTP ' + response.status + '）');
      }
      return result;
    } catch (error) {throw error.name === 'AbortError' ? Error('读取超时，已接收的数据仍然保留。请重试。') : error;}
    finally {clearTimeout(timer);}
  }
  function renderIdentity() {setText('insight-identity', session.role === 'admin' ? '管理员 · 可发起分析' : session.csrf ? '已登录 · 查看会话' : '游客 · 查看公开会话');}
  function filteredItems() {
    const query = $('insight-search').value.trim().toLocaleLowerCase(), source = $('insight-source').value, state = $('insight-state').value;
    return items.filter(item => (source === 'all' || value(item.source).toLowerCase() === source)
      && (state === 'all' || (state === 'pending' ? ['waiting','queued','running'].includes(stateOf(item)) : stateOf(item) === state))
      && (!query || [titleOf(item), item.sessionId, item.deviceId, item.deviceName, item.report?.summary].map(x => value(x).toLocaleLowerCase()).some(x => x.includes(query))));
  }
  function renderList() {
    const filtered = filteredItems(), pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    page = Math.min(page, pages - 1);
    const rows = filtered.slice(page * pageSize, (page + 1) * pageSize).map(item => {
      const row = el('tr'), cell = el('td'), heading = el('div', null, 'insight-session-title');
      const link = el('a', titleOf(item)); link.href = '?insight=' + encodeURIComponent(item.id);
      link.addEventListener('click', event => {if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button) return; event.preventDefault(); openDetail(item.id);});
      heading.append(link); cell.append(heading);
      if (item.report?.summary) cell.append(el('p', (previousAnalysis(item) ? '上次分析：' : '') + excerpt(item.report.summary, 160), 'insight-session-summary'));
      const meta = el('div', null, 'insight-session-meta');
      meta.append(el('span', sourceName(item.source)), el('span', item.deviceName || '设备 ' + value(item.deviceId).slice(0, 8)));
      cell.append(meta);
      const status = el('td'); status.append(badge(item));
      if (item.analyzedAt) status.append(el('div', dateText(item.analyzedAt).slice(5, 16), 'insight-session-meta'));
      row.append(cell, status, el('td', records(item)), dateCell(item.sourceTime), dateCell(item.lastReceived));
      return row;
    });
    $('insight-rows').replaceChildren(...rows);
    $('insight-empty').hidden = rows.length > 0;
    setText('insight-empty', items.length ? (hasMore ? '已读取的会话中没有匹配。可以加载更多历史会话后继续查找。' : '没有匹配的会话。试试其他关键词或分析状态。') : '尚未接收到 SessionLens 会话。采集数据到达后，会在这里显示接收和分析状态。');
    setText('insight-list-count', hasMore ? `已读取 ${items.length} / ${totalSessions} 个会话` : filtered.length.toLocaleString('zh-CN') + ' 个会话');
    setText('insight-page-position', filtered.length ? `${page * pageSize + 1}–${Math.min((page + 1) * pageSize, filtered.length)} / ${filtered.length}` : '0 个会话');
    $('insight-prev-page').disabled = page === 0; $('insight-next-page').disabled = page >= pages - 1;
    $('insight-load-more').hidden = !hasMore;
  }
  function renderSummary(summary) {
    const pending = items.filter(i => ['waiting','queued','running','stale'].includes(stateOf(i))).length;
    const complete = items.filter(i => stateOf(i) === 'completed').length;
    setText('insight-received-count', Number(summary?.receivedSessions ?? items.length).toLocaleString('zh-CN'));
    setText('insight-pending-count', Number(summary?.pending ?? pending).toLocaleString('zh-CN'));
    setText('insight-completed-count', Number(summary?.completed ?? complete).toLocaleString('zh-CN'));
    setText('insight-latest-received', dateText(summary?.latestReceived));
    const latest = [...items].sort((a,b) => timeValue(b.lastReceived) - timeValue(a.lastReceived))[0];
    const source = latest && timeValue(latest.lastReceived) === timeValue(summary?.latestReceived) ? `${sourceName(latest.source)} · ${latest.deviceName || '设备 ' + value(latest.deviceId).slice(0, 8)} · ` : '';
    setText('insight-reception-text', latest ? `${source}最近接收 ${dateText(summary?.latestReceived || latest.lastReceived)}。列表按任务记录时间排序；历史同步与新任务分别显示。` : '还没有会话接收记录。');
  }
  async function loadList(options = {}) {
    if (loading) return;
    loading = true;
    if (!options.background) $('insight-refresh').disabled = true;
    try {
      const data = await api('/api/insights');
      const incoming = asArray(data.items);
      items = [...new Map([...items, ...incoming].map(item => [item.id, item])).values()].sort((a,b) => timeValue(b.sourceTime) - timeValue(a.sourceTime));
      session = data.session || session; totalSessions = Number(data.summary?.receivedSessions ?? items.length); automation = asArray(data.automation);
      if (!nextOffset) nextOffset = Number(data.nextOffset || incoming.length);
      hasMore = Boolean(data.hasMore) && items.length < totalSessions;
      renderIdentity(); renderSummary(data.summary); updateAnalyzeAction(); renderAutomation();
      const signature = JSON.stringify(items);
      if (signature !== listSignature || options.force) {listSignature = signature; renderList();}
      clearError();
      if (selectedId && detail) {
        const current = items.find(i => i.id === selectedId);
        if (!incoming.some(item => item.id === selectedId) || (current && (current.revision !== detail.revision || current.state !== detail.state || current.eligible !== detail.eligible || current.stage !== detail.stage || current.analyzedRevision !== detail.analyzedRevision || current.analyzedAt !== detail.analyzedAt))) await readDetail(selectedId, true);
      }
    } catch (error) {showError(error); if (!items.length) {setText('insight-empty', '未能读取会话。请点击重新读取。'); $('insight-empty').hidden = false;}}
    finally {loading = false; $('insight-refresh').disabled = false;}
  }
  async function loadMore() {
    if (loading || !hasMore) return;
    loading = true; $('insight-load-more').disabled = true;
    try {
      const data = await api('/api/insights?offset=' + encodeURIComponent(nextOffset));
      items = [...new Map([...items,...asArray(data.items)].map(item => [item.id,item])).values()].sort((a,b) => timeValue(b.sourceTime) - timeValue(a.sourceTime));
      nextOffset = Number(data.nextOffset || nextOffset + asArray(data.items).length); totalSessions = Number(data.summary?.receivedSessions ?? totalSessions);
      hasMore = Boolean(data.hasMore); listSignature = JSON.stringify(items); renderList(); renderSummary(data.summary); renderAutomation(); clearError();
    } catch (error) {showError(error);} finally {loading = false; $('insight-load-more').disabled = false;}
  }
  function selectedAutomation() {
    let pair;
    try {pair = JSON.parse($('insight-automation-source').value || 'null');} catch (_) {return null;}
    if (!Array.isArray(pair) || pair.length !== 2) return null;
    return {deviceId:pair[0],source:pair[1],setting:automation.find(item => item.deviceId === pair[0] && item.source === pair[1])};
  }
  function updateAutomationAction(resetConsent = false) {
    const selected = selectedAutomation(), enabled = Boolean(selected?.setting?.enabled);
    const input = $('insight-automation-budget'), source = $('insight-automation-source').value;
    if (source !== budgetSource || resetConsent) {budgetSource = source; input.value = Number(selected?.setting?.dailyBudgetCNY ?? 1).toFixed(2);}
    const budgetChanged = enabled && Number(input.value) !== Number(selected?.setting?.dailyBudgetCNY ?? 1);
    $('insight-automation-submit').disabled = !selected || automationSubmitting;
    setText('insight-automation-submit', enabled ? '关闭自动分析' : '开启自动分析');
    setText('insight-automation-state', enabled ? '已开启' : '未开启');
    $('insight-automation-state').className = 'insight-status ' + (enabled ? 'completed' : 'waiting');
    $('insight-automation-consent').hidden = enabled && !budgetChanged;
    $('insight-automation-confirmed').required = false;
    $('insight-automation-save-budget').hidden = !enabled;
    $('insight-automation-save-budget').disabled = !budgetChanged || automationSubmitting;
    setText('insight-automation-reserved', enabled ? '今日已估算保留 ¥' + Number(selected?.setting?.estimatedReservedTodayCNY ?? 0).toFixed(2) : '默认 ¥1.00，可设置 ¥0.10–100.00');
    if (resetConsent) {$('insight-automation-confirmed').checked = false; setText('insight-automation-result', '');}
  }
  function renderAutomation() {
    const enabled = automation.filter(item => item.enabled);
    setText('insight-automation-summary', enabled.length ? enabled.length + ' 个采集来源已开启' : '未开启');
    const admin = session.role === 'admin' && Boolean(session.csrf);
    $('insight-automation-form').hidden = !admin; $('insight-automation-readonly').hidden = Boolean(admin);
    if (!admin) return;
    const pairs = new Map();
    for (const item of [...items, ...automation]) {
      if (!item.deviceId || !['codex','workbuddy'].includes(item.source)) continue;
      const key = JSON.stringify([item.deviceId,item.source]);
      if (!pairs.has(key)) pairs.set(key, (item.deviceName || '设备 ' + value(item.deviceId).slice(0,8)) + ' · ' + sourceName(item.source));
    }
    const signature = JSON.stringify([...pairs]);
    if (signature !== automationOptions) {
      automationOptions = signature;
      const previous = $('insight-automation-source').value;
      $('insight-automation-source').replaceChildren(...[...pairs].map(([key,label]) => {const option = el('option', label); option.value = key; return option;}));
      $('insight-automation-source').value = pairs.has(previous) ? previous : [...pairs.keys()][0] || '';
    }
    updateAutomationAction();
  }
  function stopPlayback() {
    if (playTimer) clearTimeout(playTimer);
    playTimer = null; setText('insight-play', '播放过程'); $('insight-play').setAttribute('aria-pressed', 'false');
  }
  function selectTab(tab) {
    activeTab = ['process','findings','evidence'].includes(tab) ? tab : 'process';
    for (const key of ['process','findings','evidence']) {
      $('insight-' + key).hidden = key !== activeTab;
      $('insight-tab-' + key).setAttribute('aria-selected', String(key === activeTab));
      $('insight-tab-' + key).tabIndex = key === activeTab ? 0 : -1;
    }
    if (activeTab !== 'process') stopPlayback();
  }
  function updateAnalyzeAction() {
    const button = $('insight-analyze'), item = detail;
    button.hidden = !item || item.eligible === false || !canAnalyze(item);
    button.disabled = !item || ['queued','running','completed'].includes(stateOf(item));
    button.textContent = stateOf(item) === 'failed' ? '重试分析' : stateOf(item) === 'stale' ? '补充分析新记录' : stateOf(item) === 'completed' ? '当前记录已分析' : '分析这次会话';
  }
  function detailNotice(item) {
    if (item.eligible === false) return '这批记录只有授权、环境或状态信息，尚未采集到可分析的实际任务过程。';
    if (stateOf(item) === 'stale') return '当前会话记录还有内容等待补充分析。下方结论和执行过程均来自上次分析，尚未覆盖当前记录版本。';
    if (stateOf(item) === 'failed') return '本次分析未完成。' + excerpt(item.error || '可以重试；接收的会话记录不会因此丢失。', 180);
    if (['queued','running'].includes(stateOf(item))) {const stage = {plan:'正在理解任务范围',analyze:'正在整理过程和检查发现',review:'正在核对结论与原文'}[item.stage] || '分析完成后会在这里更新结果'; return item.report ? `${stage}。下方仍为上一次完成的结论。` : `接收记录已保存。${stage}。`;}
    if (stateOf(item) === 'waiting') return canAnalyze(item) ? '记录已接收。点击“分析这次会话”，确认本次片段后开始分析。' : '记录已接收，具有权限的账号尚未发起分析。';
    return '';
  }
  function deliveryBadge(completion) {
    const map = {completed:['已交付 · 待核验','ap-status-attention'], partial:['部分交付','ap-status-attention'], failed:['任务失败','ap-status-danger'], unknown:['交付尚未确认','ap-status-attention']};
    const pair = map[completion] || map.unknown;
    return el('span', pair[0], 'pill ' + pair[1]);
  }
  function renderDetail(item, preserve = false) {
    const opened = preserve ? [...$('insight-evidence-list').children].filter(n => n.open).map(n => n.dataset.evidenceId) : [];
    detail = item;
    if (!preserve) {currentStep = 0; activeTab = 'process'; evidenceLimit = 20;}
    stopPlayback();
    $('insight-answer').setAttribute('aria-busy', 'false');
    setText('insight-detail-source', sourceName(item.source) + ' · ' + (item.deviceName || '设备 ' + value(item.deviceId).slice(0,8)));
    setText('insight-detail-title', titleOf(item));
    $('insight-previous-analysis').hidden = !previousAnalysis(item);
    setText('insight-previous-analysis', previousAnalysis(item) ? '上次分析 · ' + (item.report?.title || item.report?.goal || '此前会话记录') : '');
    $('insight-detail-state').replaceChildren(badge(item));
    $('insight-delivery-state').replaceChildren(...(item.report ? [deliveryBadge(item.report.completion)] : []));
    setText('insight-summary', item.report?.summary || (item.eligible === false ? '已接收环境与授权记录，尚没有可回顾的实际执行过程。' : ['queued','running'].includes(stateOf(item)) ? '正在根据会话记录整理任务过程与交付结果。' : stateOf(item) === 'failed' ? '分析未完成，暂时没有本次结论。' : '已接收到这次会话，还没有分析结论。'));
    $('insight-outcome-block').hidden = !item.report?.outcome;
    setText('insight-outcome', item.report?.outcome);
    $('insight-detail-meta').replaceChildren(el('span', '已接收 ' + records(item) + ' 条记录'), el('span', '任务记录 ' + dateText(item.sourceTime)), el('span', '最近接收 ' + dateText(item.lastReceived)), ...(item.analyzedAt ? [el('span', '完成分析 ' + dateText(item.analyzedAt))] : []));
    setText('insight-detail-notice', detailNotice(item)); updateAnalyzeAction();
    const story = asArray(item.report?.story);
    currentStep = Math.min(currentStep, Math.max(0, story.length - 1));
    $('insight-steps').replaceChildren(...story.map((step, index) => {
      const button = el('button'); button.type = 'button'; button.append(el('span', index + 1), el('strong', step.title || '步骤 ' + (index + 1)));
      button.setAttribute('aria-pressed', String(index === currentStep)); button.addEventListener('click', () => {stopPlayback(); showStep(index);}); return button;
    }));
    $('insight-play').disabled = story.length < 2; $('insight-next-step').disabled = story.length < 2;
    showStep(currentStep); renderFindings(); renderEvidence(opened); renderNewEvidence(); renderLimits(); selectTab(activeTab);
    detailSignature = JSON.stringify(item);
  }
  function evidenceByRef(ref) {return asArray(detail?.evidence).find(e => e.evidenceId === ref);}
  function referenceLinks(refs) {
    const container = el('div', null, 'insight-ref-list');
    const valid = [...new Set(asArray(refs))].filter(ref => evidenceByRef(ref));
    if (!valid.length) return container;
    container.append(el('span', '原始依据', 'insight-ref-label'));
    for (const ref of valid) {const button = el('button', ref); button.type = 'button'; button.addEventListener('click', () => revealEvidence(ref)); container.append(button);}
    return container;
  }
  function structuredText(text) {
    if (typeof text === 'object' && text !== null) return JSON.stringify(text, null, 2);
    try {return JSON.stringify(JSON.parse(value(text)), null, 2);} catch (_) {return value(text);}
  }
  function toolName(record) {
    if (record?.tool || record?.toolName) return value(record.tool || record.toolName);
    try {const parsed = JSON.parse(value(evidenceText(record))); return value(parsed.tool || parsed.toolName || parsed.name || parsed.payload?.name, '工具调用');}
    catch (_) {return '工具调用';}
  }
  function rawDisclosure(label, text) {
    const disclosure = el('details'); disclosure.append(el('summary', label), el('pre', structuredText(text))); return disclosure;
  }
  function showStep(index) {
    const story = asArray(detail?.report?.story), card = $('insight-step-content');
    currentStep = Math.max(0, Math.min(index, story.length - 1));
    [...$('insight-steps').children].forEach((button, i) => button.setAttribute('aria-pressed', String(i === currentStep)));
    card.replaceChildren();
    if (!story.length) {card.append(el('p', detail?.report ? '这次分析尚未列出可回放的执行步骤。可以查看原始依据。' : '任务过程将在分析完成后显示。已接收的原始依据可以先查看。', 'muted')); return;}
    const step = story[currentStep];
    card.append(el('h3', step.title || '步骤 ' + (currentStep + 1)));
    const flow = el('div', null, 'insight-step-fields'), action = el('section'), result = el('section');
    action.append(el('span', 'Agent 做了什么', 'insight-label'), el('p', step.action || '未记录动作摘要'));
    result.append(el('span', '返回与结果', 'insight-label'), el('p', step.result || '未记录结果摘要'));
    flow.append(action, result); card.append(flow);
    const cited = asArray(step.evidenceRefs).map(evidenceByRef).filter(Boolean);
    const call = cited.find(e => e.kind === 'tool_call');
    if (call) {
      const returned = call.callId ? (cited.find(e => e.kind === 'tool_result' && e.callId === call.callId) || asArray(detail.evidence).find(e => e.kind === 'tool_result' && e.callId === call.callId)) : null;
      const tools = el('div', null, 'insight-tool-flow'), request = el('section'), response = el('section');
      request.append(el('span', '调用工具', 'insight-label'), el('h4', toolName(call)), el('p', excerpt(structuredText(evidenceText(call)), 200), 'insight-preview'), rawDisclosure('查看工具参数', evidenceText(call)));
      response.append(el('span', '工具返回', 'insight-label'), el('h4', returned ? '已记录返回' : call.callId ? '分析片段中没有匹配返回' : '缺少调用标识，未关联返回'), el('p', returned ? excerpt(structuredText(evidenceText(returned)), 200) : '仅有调用记录，不能据此确认工具执行成功。', 'insight-preview'));
      if (returned) response.append(rawDisclosure('查看完整返回片段', evidenceText(returned)));
      tools.append(request, el('span', '→', 'insight-flow-arrow'), response); card.append(tools);
    }
    card.append(referenceLinks(step.evidenceRefs));
    card.classList.remove('insight-step-content-update');
    void card.offsetWidth; card.classList.add('insight-step-content-update');
  }
  function renderFindings() {
    const findings = asArray(detail?.report?.findings), list = $('insight-finding-list');
    list.replaceChildren();
    if (!findings.length) {list.append(el('article', detail?.report ? '当前分析没有列出需处理的发现。分析范围和未记录的行为可在下方查看。' : '会话尚未完成分析，当前没有安全结论。', 'card insight-empty')); return;}
    for (const finding of findings) {
      const card = el('article', null, 'card insight-finding'), heading = el('div', null, 'insight-finding-heading');
      const severity = {critical:'严重', high:'高风险', medium:'中风险', low:'低风险', info:'提示'}[finding.severity] || '待核实';
      heading.append(el('span', severity, 'insight-severity ' + (Object.hasOwn({critical:1, high:1, medium:1, low:1, info:1}, finding.severity) ? finding.severity : '')), el('h3', finding.title || '需要检查的发现'));
      if (finding.category) heading.append(el('span', {security:'安全',reliability:'交付可靠性',workflow:'执行效率'}[finding.category] || finding.category, 'insight-label'));
      card.append(heading, el('p', finding.fact || '未提供事实摘要'));
      const fields = el('div', null, 'insight-step-fields'), impact = el('section'), remediation = el('section');
      impact.append(el('span', '可能的影响', 'insight-label'), el('p', finding.impact || '未提供影响说明'));
      remediation.append(el('span', '处理方法', 'insight-label'), el('p', finding.remediation || '需要进一步核实处理方法'));
      fields.append(impact, remediation); card.append(fields, referenceLinks(finding.evidenceRefs));
      if (finding.status) card.append(el('p', finding.status === 'hypothesis' ? '待验证线索' : finding.status === 'observed' ? '来源中有记录，处置前请核对原始依据' : finding.status, 'insight-evidence-meta'));
      list.append(card);
    }
  }
  function renderEvidence(opened = []) {
    const evidence = asArray(detail?.evidence), list = $('insight-evidence-list');
    setText('insight-evidence-count', evidence.length + ' 个分析片段 · 原始会话 ' + records(detail) + ' 条记录');
    list.replaceChildren(...evidence.slice(0, evidenceLimit).map(record => {
      const node = el('details', null, 'card insight-evidence-item'); node.dataset.evidenceId = value(record.evidenceId); node.open = opened.includes(record.evidenceId);
      const summary = el('summary'); summary.append(el('strong', record.evidenceId || '来源片段'), el('span', kinds[record.kind] || record.kind || '记录', 'insight-label'));
      if (record.kind === 'tool_call') summary.append(el('span', toolName(record), 'insight-label'));
      node.append(summary, el('p', dateText(record.timestamp) + ' · 原始事件 ' + value(record.eventId, '未记录') + (record.callId ? ' · 调用 ' + record.callId : ''), 'insight-evidence-meta'));
      if (record.kind === 'reasoning') node.append(el('p', '应用日志中保留的思路记录；不代表模型全部思考过程。', 'insight-evidence-meta'));
      node.append(el('pre', structuredText(evidenceText(record)))); return node;
    }));
    if (!evidence.length) list.append(el('p', '目前没有可展示的分析片段。会话接收状态不等于已完成分析。', 'muted'));
    $('insight-evidence-more').hidden = evidence.length <= evidenceLimit;
  }
  function renderNewEvidence() {
    const preview = detail?.report ? asArray(detail?.currentPreview) : [];
    $('insight-new-evidence').hidden = !preview.length;
    $('insight-new-evidence-list').replaceChildren(...preview.map(record => rawDisclosure((kinds[record.kind] || record.kind || '记录') + ' · ' + dateText(record.timestamp), evidenceText(record))));
  }
  function revealEvidence(ref) {
    const index = asArray(detail?.evidence).findIndex(e => e.evidenceId === ref);
    if (index < 0) return;
    const opened = [...$('insight-evidence-list').children].filter(n => n.open).map(n => n.dataset.evidenceId);
    evidenceLimit = Math.max(evidenceLimit, index + 1); renderEvidence([...opened, ref]); selectTab('evidence');
    const node = [...$('insight-evidence-list').children].find(n => n.dataset.evidenceId === ref);
    if (node) {node.open = true; node.classList.add('selected-evidence'); node.scrollIntoView({block:'nearest', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});}
  }
  function renderLimits() {
    $('insight-limit-list').replaceChildren(...[...new Set([...asArray(detail?.limitations), ...asArray(detail?.report?.limitations)])].map(text => el('p', text)));
    if (!$('insight-limit-list').children.length) $('insight-limit-list').append(el('p', '分析只依据已采集会话记录；没有记录的网络、进程或文件行为，不能据此确认。'));
    const fields = [['会话标识', detail?.sessionId], ['设备标识', detail?.deviceId], ['当前记录版本', detail?.revision], ['已分析版本', detail?.analyzedRevision || '尚未分析'], ['本次选择片段', detail?.includedEvents == null ? '未记录' : detail.includedEvents + ' 个'], ['分析完成时间', dateText(detail?.analyzedAt)]];
    $('insight-source-details').replaceChildren(...fields.flatMap(([label, text]) => [el('dt', label), el('dd', text)]));
  }
  async function readDetail(id, preserve = false) {
    const generation = ++detailRequest;
    try {
      const response = await api('/api/insights/' + encodeURIComponent(id));
      if (generation !== detailRequest || selectedId !== id) return;
      const item = response.item || response;
      if (JSON.stringify(item) !== detailSignature || !preserve) renderDetail(item, preserve);
      if (!preserve && location.hash) {let ref;try {ref = decodeURIComponent(location.hash.slice(1));}catch (_) {ref = '';}if (ref) revealEvidence(ref);}
      clearError();
    } catch (error) {if (generation === detailRequest && selectedId === id) {showError(error); $('insight-answer').setAttribute('aria-busy', 'false'); if (!detail) {setText('insight-summary', '未能读取这次会话，请重试。'); setText('insight-detail-notice', '已接收记录仍保留在平台中。');}}}
  }
  async function openDetail(id, changeHistory = true) {
    if (!id) return;
    stopPlayback(); selectedId = value(id); detail = null; detailSignature = ''; activeTab = 'process'; currentStep = 0;
    $('insight-list-view').hidden = true; $('insight-detail-view').hidden = false;
    $('insight-answer').setAttribute('aria-busy', 'true'); setText('insight-summary', '正在读取这次会话…');
    setText('insight-detail-title', '会话详情'); setText('insight-detail-source', ''); setText('insight-outcome', ''); $('insight-previous-analysis').hidden = true; setText('insight-previous-analysis', '');
    $('insight-outcome-block').hidden = true; $('insight-detail-state').replaceChildren(); $('insight-delivery-state').replaceChildren(); $('insight-detail-meta').replaceChildren();
    setText('insight-detail-notice', ''); $('insight-steps').replaceChildren(); $('insight-step-content').replaceChildren(); $('insight-finding-list').replaceChildren(); $('insight-evidence-list').replaceChildren(); $('insight-limit-list').replaceChildren(); $('insight-source-details').replaceChildren(); $('insight-new-evidence-list').replaceChildren(); $('insight-new-evidence').hidden = true;
    selectTab('process'); updateAnalyzeAction();
    if (changeHistory) history.pushState({}, '', location.pathname + '?insight=' + encodeURIComponent(selectedId));
    await readDetail(selectedId);
  }
  function closeDetail(changeHistory = true) {
    ++detailRequest; stopPlayback(); selectedId = ''; detail = null; detailSignature = '';
    $('insight-detail-view').hidden = true; $('insight-list-view').hidden = false;
    if (changeHistory) history.pushState({}, '', location.pathname);
  }
  function schedulePlayback() {
    playTimer = setTimeout(() => {
      const story = asArray(detail?.report?.story);
      if (currentStep >= story.length - 1 || document.hidden) {stopPlayback(); return;}
      showStep(currentStep + 1); schedulePlayback();
    }, 2200 / Number($('insight-speed').value || 1));
  }
  $('insight-play').addEventListener('click', () => {
    if (playTimer) {stopPlayback(); return;}
    if (asArray(detail?.report?.story).length < 2) return;
    if (currentStep >= detail.report.story.length - 1) showStep(0);
    setText('insight-play', '暂停回放'); $('insight-play').setAttribute('aria-pressed', 'true'); schedulePlayback();
  });
  $('insight-next-step').addEventListener('click', () => {stopPlayback(); const count = asArray(detail?.report?.story).length; if (count) showStep((currentStep + 1) % count);});
  $('insight-speed').addEventListener('change', () => {if (playTimer) {clearTimeout(playTimer); schedulePlayback();}});
  for (const key of ['process','findings','evidence']) {
    const button = $('insight-tab-' + key); button.addEventListener('click', () => selectTab(key));
    button.addEventListener('keydown', event => {
      const keys = ['process','findings','evidence'];
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault(); const position = keys.indexOf(key);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (position + (event.key === 'ArrowRight' ? 1 : -1) + 3) % 3;
      selectTab(keys[next]); $('insight-tab-' + keys[next]).focus();
    });
  }
  $('insight-evidence-more').addEventListener('click', () => {const opened = [...$('insight-evidence-list').children].filter(n => n.open).map(n => n.dataset.evidenceId); evidenceLimit += 20; renderEvidence(opened);});
  $('insight-back').addEventListener('click', () => closeDetail());
  for (const id of ['insight-search','insight-source','insight-state']) $(id).addEventListener(id === 'insight-search' ? 'input' : 'change', () => {page = 0; renderList();});
  $('insight-prev-page').addEventListener('click', () => {page -= 1; renderList();});
  $('insight-next-page').addEventListener('click', () => {page += 1; renderList();});
  $('insight-load-more').addEventListener('click', loadMore);
  $('insight-automation-source').addEventListener('change', () => updateAutomationAction(true));
  $('insight-automation-budget').addEventListener('input', () => updateAutomationAction());
  async function saveAutomation(saveBudget = false) {
    const selected = selectedAutomation();
    if (!selected || automationSubmitting || session.role !== 'admin' || !session.csrf) return;
    const enabled = saveBudget || !selected.setting?.enabled;
    if (enabled && !$('insight-automation-confirmed').checked) {setText('insight-automation-result', '请先确认此采集来源的新任务片段可以用于分析。'); return;}
    const budget = Number($('insight-automation-budget').value);
    if (enabled && (!Number.isFinite(budget) || budget < 0.1 || budget > 100)) {setText('insight-automation-result', '每日估算预算请填写 ¥0.10–100.00。'); return;}
    automationSubmitting = true; updateAutomationAction(); setText('insight-automation-result', '正在保存设置…');
    try {
      await api('/api/insights/automation', {deviceId:selected.deviceId,source:selected.source,enabled,...(enabled ? {dailyBudgetCNY:budget,shareConfirmed:true} : {})});
      await loadList({force:true}); $('insight-automation-confirmed').checked = false;
      setText('insight-automation-result', saveBudget ? '已保存每日估算预算。后续模型调用按此预算控制。' : enabled ? '已开启。此后新任务记录会在接收稳定后进入分析，已有历史会话不会自动重扫。' : '已关闭。此来源的新任务不再自动进入分析。');
    } catch (error) {setText('insight-automation-result', error.message);}
    finally {automationSubmitting = false; updateAutomationAction();}
  }
  $('insight-automation-form').addEventListener('submit', async event => {event.preventDefault(); await saveAutomation();});
  $('insight-automation-save-budget').addEventListener('click', () => saveAutomation(true));
  const refresh = async () => {await loadList({force:true}); if (selectedId) await readDetail(selectedId, true);};
  $('insight-refresh').addEventListener('click', refresh); $('insight-retry').addEventListener('click', refresh);
  $('insight-analyze').addEventListener('click', () => {
    if (!detail || detail.eligible === false || !canAnalyze(detail) || ['queued','running','completed'].includes(stateOf(detail))) return;
    consentingId = detail.id; consentingRevision = detail.revision; setText('insight-consent-task', titleOf(detail) + ' · 原始会话 ' + records(detail) + ' 条记录' + (detail.includedEvents != null ? ' · 本次选择 ' + detail.includedEvents + ' 个片段' : ''));
    $('insight-share-confirmed').checked = false; setText('insight-consent-error', ''); $('insight-consent-submit').disabled = false; $('insight-consent').showModal();
  });
  $('insight-consent-cancel').addEventListener('click', () => {$('insight-consent').close(); consentingId = ''; consentingRevision = '';});
  $('insight-consent-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!$('insight-share-confirmed').checked || !consentingId || detail?.id !== consentingId || !canAnalyze(detail)) return;
    const id = consentingId; $('insight-consent-submit').disabled = true; $('insight-consent-cancel').disabled = true; setText('insight-consent-error', '');
    try {
      await api('/api/insights/analyze', {id, revision:consentingRevision, shareConfirmed:true});
      $('insight-consent').close(); consentingId = ''; consentingRevision = '';
      await loadList({force:true}); if (selectedId === id) await readDetail(id, true);
    } catch (error) {setText('insight-consent-error', error.message);}
    finally {$('insight-consent-submit').disabled = false; $('insight-consent-cancel').disabled = false;}
  });
  const requestedId = () => {const params = new URLSearchParams(location.search); return params.get('insight') || params.get('session');};
  addEventListener('popstate', () => {const id = requestedId(); if (id) openDetail(id, false); else closeDetail(false);});
  addEventListener('hashchange', () => {let ref; try {ref = decodeURIComponent((location.hash || '').slice(1));}catch (_) {return;} if (ref) revealEvidence(ref);});
  document.addEventListener('visibilitychange', () => {if (document.hidden) stopPlayback(); else loadList({background:true});});
  setInterval(() => {if (!document.hidden && !$('insight-consent').open) loadList({background:true});}, 20000);
  (async () => {await loadList(); const initialId = requestedId(); if (initialId) await openDetail(initialId, false);})();
})();
