/* Aggregate metadata only; evidence remains behind the existing record search. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), arr = value => Array.isArray(value) ? value : [];
  const text = value => typeof value === 'string' ? value : typeof value === 'number' ? String(value) : '';
  const node = (tag,value,cls) => {const n = document.createElement(tag); if (value != null) n.textContent = text(value); if (cls) n.className = cls; return n;};
  const put = (id,value) => {$(id).textContent = text(value);};
  const option = (value,label) => {const n = node('option',label); n.value = value; return n;};
  const types = {tool:{key:'tools',name:'工具',count:'调用记录',definition:'按已采集的工具调用记录降序排列；保留原始工具名。'},skill:{key:'skills',name:'Skill',count:'证据记录',definition:'按 Skill 加载与说明读取的记录合计排序；仅读取说明不代表已加载。'},mcp:{key:'mcps',name:'MCP',count:'调用记录',definition:'按已采集的 MCP 调用记录降序排列；直接调用与命令包装分别标注。'}};
  const scopeKeys = ['device','application','collector','after','before'];
  const pageSize = 50;
  let type = 'tool', page = 1, generation = 0, controller = null, snapshot = '', signature = '', lastData = null;
  const number = value => value != null && value !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value).toLocaleString('zh-CN') : '未提供';
  function date(value) {if (value == null || value === '') return '未采集'; let at = value; if (Number.isFinite(Number(value))) {at = Number(value); if (Math.abs(at) < 1e11) at *= 1000;} const d = new Date(at); return Number.isNaN(d.getTime()) ? '未采集' : new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(d);}
  function filters() {
    const params = {object:type,device:$('dr-device').value,application:$('dr-application').value,collector:$('dr-collector').value,page:String(page),pageSize:String(pageSize)};
    for (const key of ['after','before']) if ($('dr-' + key).value) {const d = new Date($('dr-' + key).value); if (Number.isNaN(d.getTime())) throw Error('请填写有效的时间范围。'); params[key] = d.toISOString();}
    if (params.after && params.before && params.after > params.before) throw Error('开始时间不能晚于结束时间。'); return params;
  }
  function searchLink(params,query,object) {const p = new URLSearchParams(); for (const key of scopeKeys) if (params[key] && params[key] !== 'all') p.set(key,params[key]); if (object) p.set('object',object); if (query != null) p.set('q',query); return '/model-data' + (p.size ? '?' + p : '');}
  function renderScope(params) {
    const device = $('dr-device').selectedOptions?.[0]?.textContent || '所有设备', application = $('dr-application').selectedOptions?.[0]?.textContent || '所有 Agent', collector = $('dr-collector').selectedOptions?.[0]?.textContent || '所有采集器';
    put('dr-scope',[device,application,collector,params.after ? '从 ' + date(params.after) : '',params.before ? '至 ' + date(params.before) : ''].filter(Boolean).join(' · '));
    $('dr-search-link').href = searchLink(params,'','all');
    const url = new URLSearchParams(); for (const key of scopeKeys) if (params[key] && params[key] !== 'all') url.set(key,params[key]); if (type !== 'tool') url.set('object',type);
    if (typeof history !== 'undefined' && history.replaceState) history.replaceState(null,'','/model-data/rankings' + (url.size ? '?' + url : ''));
  }
  function renderFacets(facets) {if (!Array.isArray(facets?.devices)) return; const selected = $('dr-device').value; $('dr-device').replaceChildren(option('all','所有设备'),...facets.devices.map(d => option(d.id,d.name || d.id))); if (selected !== 'all') {if (!facets.devices.some(d => d.id === selected)) $('dr-device').append(option(selected,selected)); $('dr-device').value = selected;}}
  function countDetails(item) {return type === 'skill' ? '加载 ' + number(item.loadCount) + ' · 读取说明 ' + number(item.readCount) : type === 'mcp' ? '直接 ' + number(item.directCount) + ' · 命令包装 ' + number(item.wrappedCount) : '';}
  function row(item,index,params) {
    const tr = node('tr'), rank = (page-1)*pageSize+index+1, name = node('td');
    tr.append(node('td',String(rank),'dr-rank-number' + (rank <= 3 ? ' leading' : '')));
    if (text(item.searchQuery)) {const link = node('a',item.name || '未命名对象','dr-object-link'); link.href = searchLink(params,item.searchQuery,type); link.setAttribute('aria-label','查询 ' + (item.name || '此对象') + ' 的已采记录'); name.append(link);} else name.append(node('strong',item.name || '未命名对象'));
    if (type === 'skill') name.append(node('span',Number(item.loadCount) > 0 ? '明确加载与说明读取分别计数' : '仅有说明读取证据','dr-row-basis'));
    if (type === 'mcp') name.append(node('span','原始工具名和调用参数可在记录中核对','dr-row-basis'));
    const methods = arr(item.methods);
    if (type === 'mcp' && (methods.length || item.methodsHasMore)) {const details = node('details',null,'dr-methods'); details.append(node('summary','查看方法 · 已列 ' + methods.length + ' 种')); const list = node('div',null,'dr-method-list'); for (const method of methods) {const line = node('div',null,'dr-method'); if (text(method.searchQuery)) {const link = node('a',method.name || '未命名方法'); link.href = searchLink(params,method.searchQuery,'mcp'); line.append(link);} else line.append(node('span',method.name || '未命名方法')); line.append(node('span',number(method.callCount) + ' 条')); list.append(line);} details.append(list); if (item.methodsHasMore) details.append(node('p','更多方法未在本页展开；点击 MCP 名称可检索其全部已采记录。')); name.append(details);}
    tr.append(name); const counts = node('td'); counts.append(node('span',number(type === 'skill' ? item.activityCount : item.callCount ?? item.activityCount),'dr-count')); const detail = countDetails(item); if (detail) counts.append(node('span',detail,'dr-count-detail')); tr.append(counts,node('td',number(item.deviceCount)),node('td',number(item.accountCount)),node('td',number(item.sourceSessionCount)),node('td',date(item.lastSeen),'dr-last-seen')); return tr;
  }
  function render(data,params) {
    lastData = data; const key = types[type].key, rows = arr(data[key]);
    $('dr-rows').replaceChildren(...rows.map((item,index) => row(item,index,params))); $('dr-empty').hidden = rows.length > 0;
    put('dr-total',data.totals?.[key] != null && Number.isFinite(Number(data.totals[key])) ? number(data.totals[key]) + ' 种已识别对象' : '本页 ' + rows.length + ' 种对象');
    $('dr-prev').disabled = page <= 1; $('dr-next').disabled = !data.hasMore?.[key]; put('dr-page-position',rows.length ? '第 ' + page + ' 页 · 本页 ' + rows.length + ' 种对象' : '没有匹配记录');
    const c = data.coverage || {}, coverage = []; if (Number.isFinite(c.retainedRecords)) coverage.push('平台保留 ' + number(c.retainedRecords) + ' 条记录'); if (Number.isFinite(c.searchedRecords)) coverage.push('本次统计 ' + number(c.searchedRecords) + ' 条'); if (c.indexComplete === false) coverage.push('历史索引尚在补充'); put('dr-coverage',coverage.join(' · '));
    const limitations = arr(c.limitations).filter(x => typeof x === 'string'); $('dr-coverage-details').hidden = !limitations.length; $('dr-coverage-list').replaceChildren(...limitations.map(x => node('li',x)));
    renderFacets(data.facets); renderScope(params);
  }
  async function get(path,signal) {
    const request = new AbortController(), abort = () => request.abort(); if (signal?.aborted) abort(); else signal?.addEventListener('abort',abort,{once:true});
    const timer = setTimeout(abort,30000);
    try {const response = await fetch(path,{method:'GET',credentials:'same-origin',cache:'no-store',signal:request.signal}); let data; try {data = await response.json();} catch (_) {throw Error('平台返回了无法读取的内容，请重试。');} if (!response.ok) throw Error(text(data.error) || '请求失败（HTTP ' + response.status + '）'); return data;}
    catch (e) {if (e.name === 'AbortError') throw Error(signal?.aborted ? '已停止本页等待' : '读取超时，请重试。'); throw e;} finally {clearTimeout(timer); signal?.removeEventListener('abort',abort);}
  }
  async function load(reset = false) {
    if (reset) {page = 1; snapshot = '';}
    const current = ++generation; controller?.abort(); controller = new AbortController(); const signal = controller.signal;
    $('dr-error').hidden = true; $('dr-empty').hidden = true; $('dr-rows').replaceChildren(); $('dr-rows').setAttribute('aria-busy','true'); $('dr-progress').hidden = false; $('dr-prev').disabled = true; $('dr-next').disabled = true; put('dr-total','正在读取'); put('dr-page-position',''); put('dr-coverage',''); $('dr-coverage-details').hidden = true;
    try {const params = filters(), nextSignature = JSON.stringify({...params,page:undefined}); if (signature !== nextSignature) {page = 1; params.page = '1'; snapshot = ''; signature = nextSignature;} if (snapshot) params.snapshot = snapshot; renderScope(params); const data = await get('/api/data-center/capabilities?' + new URLSearchParams(params),signal); if (current !== generation || signal.aborted) return; if (typeof data.snapshot === 'string') snapshot = data.snapshot; render(data,params);}
    catch (e) {if (current !== generation || signal.aborted) return; if (e.message.includes('快照')) {page = 1; snapshot = '';} put('dr-total','读取未完成'); put('dr-error-text',e.message + ' 当前范围已保留，可以重试。'); $('dr-error').hidden = false;}
    finally {if (current === generation) {$('dr-rows').setAttribute('aria-busy','false'); $('dr-progress').hidden = true;}}
  }
  function select(next,focus = false) {if (!types[next]) return; type = next; for (const key of Object.keys(types)) {const button = $('dr-tab-' + key); button.classList.toggle('active',key === type); button.setAttribute('aria-selected',String(key === type)); button.tabIndex = key === type ? 0 : -1;} $('dr-table-section').setAttribute('aria-labelledby','dr-tab-' + type); put('dr-title',types[type].name + '使用排行'); put('dr-name-heading',types[type].name + '名称'); put('dr-count-heading',types[type].count); put('dr-definition',types[type].definition); if (focus) $('dr-tab-' + type).focus(); load(true);}
  for (const key of Object.keys(types)) {$('dr-tab-' + key).addEventListener('click',() => select(key)); $('dr-tab-' + key).addEventListener('keydown',e => {const keys = Object.keys(types), at = keys.indexOf(type); let next; if (e.key === 'ArrowRight') next = keys[(at+1)%keys.length]; if (e.key === 'ArrowLeft') next = keys[(at+keys.length-1)%keys.length]; if (e.key === 'Home') next = keys[0]; if (e.key === 'End') next = keys.at(-1); if (next) {e.preventDefault(); select(next,true);}});}
  for (const key of ['device','application','collector']) $('dr-' + key).addEventListener('change',() => load(true));
  $('dr-apply-time').addEventListener('click',() => load(true)); $('dr-reset').addEventListener('click',() => {for (const key of ['device','application','collector']) $('dr-' + key).value = 'all'; $('dr-after').value = ''; $('dr-before').value = ''; load(true);});
  $('dr-retry').addEventListener('click',() => load()); $('dr-prev').addEventListener('click',() => {if (page > 1) {page -= 1; load();}}); $('dr-next').addEventListener('click',() => {if (lastData?.hasMore?.[types[type].key]) {page += 1; load();}});
  $('dr-cancel').addEventListener('click',() => {controller?.abort(); ++generation; $('dr-progress').hidden = true; $('dr-rows').setAttribute('aria-busy','false'); put('dr-total','已停止等待'); put('dr-error-text','已停止本页等待。排行范围已保留，可以重试。'); $('dr-error').hidden = false;});
  addEventListener('pagehide',() => {controller?.abort(); ++generation;});
  const params = new URLSearchParams(location.search); for (const key of ['application','collector']) if ([...$('dr-' + key).children].some(n => n.value === params.get(key))) $('dr-' + key).value = params.get(key);
  if (params.get('device') && params.get('device') !== 'all') {$('dr-device').append(option(params.get('device'),params.get('device'))); $('dr-device').value = params.get('device');}
  for (const key of ['after','before']) if (params.get(key)) {const d = new Date(params.get(key)); if (!Number.isNaN(d.getTime())) $('dr-' + key).value = new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
  get('/api/session').then(session => put('dr-identity',session.username ? session.username + ' · 已登录' : '游客 · 可查看已采记录')).catch(() => put('dr-identity','登录状态未取得 · 可重试排行查询'));
  select(types[params.get('object')] ? params.get('object') : 'tool');
})();
