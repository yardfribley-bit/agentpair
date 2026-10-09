/** Actual live insight controller against a synthetic DOM/API. No network or model calls. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('agentpair/web_assets/insight_live.html', 'utf8');
const controller = fs.readFileSync('agentpair/web_assets/insight_live.js', 'utf8');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.dataset = {};
    this.attributes = {}; this.hidden = false; this.disabled = false; this.open = false;
    this.value = ''; this.checked = false; this.classes = new Set(); this._text = ''; this.replacements = 0;
    this.classList = {add:(name) => this.classes.add(name), remove:(name) => this.classes.delete(name), toggle:(name,on) => on ? this.classes.add(name) : this.classes.delete(name)};
  }
  set textContent(value) {this._text = String(value ?? ''); this.children = [];}
  get textContent() {return this._text + this.children.map(n => n.textContent).join('');}
  set className(value) {this.classes = new Set(value.split(' '));}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.replacements++; this.children = [...nodes]; this._text = '';}
  setAttribute(key, value) {this.attributes[key] = String(value);}
  addEventListener(name, handler) {(this.listeners[name] ||= []).push(handler);}
  async fire(name, additions = {}) {const event = {preventDefault(){}, button:0, ...additions}; await Promise.all((this.listeners[name] || []).map(handler => handler(event)));}
  showModal() {this.open = true;}
  close() {this.open = false;}
  scrollIntoView() {this.scrolled = true;}
  focus() {this.focused = true;}
}
const nodes = new Map();
for (const match of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const node = new Element(match[1]); node.hidden = /\bhidden\b/.test(match[0]); nodes.set(match[2], node);
}
for (const id of ['insight-source','insight-state']) nodes.get(id).value = 'all';
nodes.get('insight-speed').value = '1';
const sourceRecord = (id, kind, callId, text) => ({evidenceId:id,eventId:'event-' + id,kind,callId,text,timestamp:'2026-10-07T01:30:00Z'});
let insights = [
  {id:'weather',deviceId:'mac-device',deviceName:'研发 Mac',sessionId:'weather-session',source:'workbuddy',title:'查询上海天气',eventCount:40,sourceTime:'2026-10-06T01:00:00Z',lastReceived:'2026-10-07T02:00:00Z',revision:'w1',analyzedRevision:'w1',state:'completed',analyzedAt:'2026-10-07T02:30:00Z',report:{title:'查询上海天气',goal:'查询上海天气',summary:'通过天气服务获取上海天气。',outcome:'上海 25 度；需要核对预报日期。',completion:'completed',story:[{title:'查询天气',action:'curl 请求天气服务',result:'返回上海预报',evidenceRefs:['E001','E002']},{title:'整理答案',action:'读取预报',result:'回复上海天气',evidenceRefs:['E003']}],findings:[{title:'预报时间需核对',severity:'info',status:'hypothesis',fact:'返回日期需要核对',impact:'可能误读日期',remediation:'查看原始返回',evidenceRefs:['E002']}],limitations:['没有独立核验天气准确性']},evidence:[sourceRecord('E001','tool_call','call-weather',JSON.stringify({name:'curl',arguments:{url:'https://weather.example.test/Shanghai'}})),sourceRecord('E002','tool_result','call-weather','{"city":"Shanghai","temperature":25}'),sourceRecord('E003','reasoning',null,'根据已记录天气信息整理回答。')]},
  {id:'ssh',deviceId:'windows-device',deviceName:'办公 Windows',sessionId:'ssh-session',source:'codex',title:'生成 SSH 视频',eventCount:22,sourceTime:'2026-10-07T01:00:00Z',lastReceived:'2026-10-07T03:00:00Z',revision:'s2',analyzedRevision:'s1',state:'stale',report:{title:'生成 SSH 视频',summary:'调用视频工具生成 SSH 动画。',outcome:'已交付视频。',completion:'completed',story:[{title:'生成动画',action:'调用 videoGen',result:'返回视频链接',evidenceRefs:['S001']}],findings:[],limitations:[]},evidence:[sourceRecord('S001','tool_call','call-ssh','videoGen({"prompt":"SSH animation"})')]},
  {id:'pending',deviceId:'mac-device',deviceName:'研发 Mac',sessionId:'pending-session',source:'workbuddy',title:'新的开发任务',eventCount:9,sourceTime:1791353879085,lastReceived:'2026-10-07T04:00:00Z',revision:'p1',state:'waiting',evidence:[sourceRecord('P001','user_message',null,'<script>window.leak=true</script>')]}
];
insights = insights.map(item => ({...item,permissions:{canAnalyze:true}}));
insights[1].title = '查询上海天气（新任务）';
let account = {role:'admin',csrf:'synthetic-csrf'}, automation = [], extraItems = [];
const requests = [], intervals = [], browserListeners = {}, documentListeners = {};
const location = {pathname:'/session-insights/',search:''};
let failingDetail = null;
const context = {document:{getElementById:id => nodes.get(id),createElement:tag => new Element(tag),hidden:false,addEventListener:(name, fn) => documentListeners[name] = fn},
  location, history:{pushState(_a,_b,path){const [pathname,search] = path.split('?'); location.pathname = pathname; location.search = search ? '?' + search : '';}},
  fetch:async (url, options) => {
    requests.push({url,options});
    if (url === '/api/insights' || url.startsWith('/api/insights?')) {const more = url.includes('offset='); return {ok:true,json:async () => ({items:(more ? extraItems : insights).map(({evidence,...item}) => item),session:account,automation,hasMore:!more && extraItems.length > 0,nextOffset:3 + (more ? extraItems.length : 0),summary:{receivedSessions:3 + extraItems.length,pending:2,completed:1,latestReceived:'2026-10-07T04:00:00Z'}})};}
    if (url === '/api/insights/automation') {const body = JSON.parse(options.body); automation = [{deviceId:body.deviceId,source:body.source,enabled:body.enabled,enabledAt:'2026-10-07T05:00:00Z',dailyBudgetCNY:body.dailyBudgetCNY ?? 1,estimatedReservedTodayCNY:0.2}]; return {ok:true,json:async () => ({automation})};}
    if (url === '/api/insights/analyze') {const body = JSON.parse(options.body); insights = insights.map(item => item.id === body.id ? {...item,state:'queued'} : item); return {ok:true,json:async () => ({id:body.id,state:'queued'})};}
    const id = decodeURIComponent(url.slice('/api/insights/'.length));
    if (failingDetail === id) return {ok:false,status:503,json:async () => ({error:'Temporary synthetic failure'})};
    const item = insights.find(item => item.id === id);
    return {ok:true,json:async () => ({item})};
  }, setTimeout,clearTimeout,setInterval:fn => {intervals.push(fn);return intervals.length;},clearInterval(){}, AbortController,URLSearchParams,Intl,Date,Number,JSON,Object,String,Set,console,
  addEventListener:(name,fn) => browserListeners[name] = fn,matchMedia:() => ({matches:true})};
vm.createContext(context); vm.runInContext(controller, context);
const settle = async () => {for (let i=0;i<6;i++) await new Promise(resolve => setImmediate(resolve));};
const find = (node, predicate) => node.children.flatMap(n => [...(predicate(n) ? [n] : []),...find(n,predicate)]);
const openRow = async index => {const row = nodes.get('insight-rows').children[index]; const link = find(row,n => n.tagName === 'A')[0]; await link.fire('click'); await settle();};
(async () => {
  await settle();
  assert.equal(nodes.get('insight-received-count').textContent,'3');
  assert.match(nodes.get('insight-latest-received').textContent,/2026-10-07 12:00:00/);
  assert.match(nodes.get('insight-rows').textContent,/上海天气/); assert.match(nodes.get('insight-rows').textContent,/SSH/); assert.match(nodes.get('insight-rows').textContent,/新的开发任务/);
  const renderCount = nodes.get('insight-rows').replacements; await intervals[0](); await settle();
  assert.equal(nodes.get('insight-rows').replacements,renderCount,'unchanged background list does not redraw');
  assert.equal(nodes.get('insight-automation-form').hidden,false);
  const consentPosts = requests.filter(r => r.options.method === 'POST').length;
  await nodes.get('insight-automation-form').fire('submit'); assert.equal(requests.filter(r => r.options.method === 'POST').length,consentPosts,'automatic analysis is not enabled without consent');
  assert.equal(nodes.get('insight-automation-budget').value,'1.00');
  nodes.get('insight-automation-confirmed').checked = true;
  nodes.get('insight-automation-budget').value = '100.01'; await nodes.get('insight-automation-form').fire('submit'); assert.equal(requests.filter(r => r.options.method === 'POST').length,consentPosts,'invalid budget does not enable analysis');
  nodes.get('insight-automation-budget').value = '2.50'; await nodes.get('insight-automation-form').fire('submit'); await settle();
  const enabledRequest = requests.find(r => r.url === '/api/insights/automation'); assert.deepEqual(JSON.parse(enabledRequest.options.body),{deviceId:'mac-device',source:'workbuddy',enabled:true,dailyBudgetCNY:2.5,shareConfirmed:true});
  assert.match(nodes.get('insight-automation-summary').textContent,/1 个采集来源已开启/);
  assert.match(nodes.get('insight-automation-reserved').textContent,/0.20/);
  nodes.get('insight-automation-budget').value = '3.00'; await nodes.get('insight-automation-budget').fire('input'); assert.equal(nodes.get('insight-automation-consent').hidden,false);
  nodes.get('insight-automation-confirmed').checked = true; await nodes.get('insight-automation-save-budget').fire('click'); await settle();
  assert.equal(JSON.parse(requests.filter(r => r.url === '/api/insights/automation').at(-1).options.body).dailyBudgetCNY,3);
  nodes.get('insight-automation-confirmed').checked = false; await nodes.get('insight-automation-form').fire('submit'); await settle();
  const disabledRequest = requests.filter(r => r.url === '/api/insights/automation').at(-1); assert.deepEqual(JSON.parse(disabledRequest.options.body),{deviceId:'mac-device',source:'workbuddy',enabled:false});
  const openById = async id => {const link = find(nodes.get('insight-rows'),n => n.tagName === 'A' && n.href.endsWith('?insight=' + id))[0]; await link.fire('click'); await settle();};
  await openById('weather');
  assert.match(nodes.get('insight-summary').textContent,/上海天气/); assert.doesNotMatch(nodes.get('insight-summary').textContent,/SSH/);
  assert.match(nodes.get('insight-step-content').textContent,/curl.*https:\/\/weather\.example\.test\/Shanghai.*已记录返回.*temperature/s);
  assert.equal(nodes.get('insight-analyze').hidden,false);
  const detailRenderCount = nodes.get('insight-step-content').replacements;
  await intervals[0](); await settle(); assert.equal(nodes.get('insight-step-content').replacements,detailRenderCount,'unchanged detail is preserved');
  await nodes.get('insight-next-step').fire('click'); assert.match(nodes.get('insight-step-content').textContent,/整理答案/);
  const refs = find(nodes.get('insight-step-content'), n => n.tagName === 'BUTTON'); await refs[0].fire('click');
  assert.equal(nodes.get('insight-evidence').hidden,false); assert.equal(nodes.get('insight-evidence-list').children[2].open,true);
  assert.match(nodes.get('insight-evidence-list').children[2].textContent,/不代表模型全部思考过程/);
  await nodes.get('insight-back').fire('click');
  account = {role:'user',csrf:'synthetic-csrf'}; await intervals[0](); await settle();
  assert.equal(nodes.get('insight-automation-form').hidden,true,'automatic settings remain administrator-only');
  await openById('ssh');
  assert.match(nodes.get('insight-summary').textContent,/SSH 动画/); assert.doesNotMatch(nodes.get('insight-summary').textContent,/上海天气/);
  assert.match(nodes.get('insight-detail-title').textContent,/查询上海天气（新任务）/); assert.doesNotMatch(nodes.get('insight-detail-title').textContent,/SSH/);
  assert.match(nodes.get('insight-previous-analysis').textContent,/上次分析.*SSH/);
  assert.match(nodes.get('insight-detail-notice').textContent,/尚未覆盖当前记录版本/);
  assert.equal(nodes.get('insight-analyze').hidden,false,'ordinary device owner can analyze when API permission allows');
  await nodes.get('insight-analyze').fire('click'); assert.equal(nodes.get('insight-consent').open,true);
  assert.match(nodes.get('insight-consent-task').textContent,/查询上海天气（新任务）/); assert.doesNotMatch(nodes.get('insight-consent-task').textContent,/SSH/);
  const before = requests.filter(r => r.options.method === 'POST').length;
  await nodes.get('insight-consent-form').fire('submit'); assert.equal(requests.filter(r => r.options.method === 'POST').length,before,'no model request without explicit consent');
  nodes.get('insight-share-confirmed').checked = true; await nodes.get('insight-consent-form').fire('submit'); await settle();
  const sent = requests.find(r => r.url === '/api/insights/analyze'); assert.deepEqual(JSON.parse(sent.options.body),{id:'ssh',revision:'s2',shareConfirmed:true}); assert.equal(sent.options.headers['X-CSRF-Token'],'synthetic-csrf');
  assert.equal(nodes.get('insight-consent').open,false); assert.equal(nodes.get('insight-analyze').disabled,true); assert.match(nodes.get('insight-detail-state').textContent,/已排队/);
  await nodes.get('insight-back').fire('click'); await openById('pending');
  assert.match(nodes.get('insight-summary').textContent,/还没有分析结论/); assert.doesNotMatch(nodes.get('insight-summary').textContent,/SSH|上海/);
  assert.match(nodes.get('insight-evidence-list').textContent,/<script>window.leak=true<\/script>/); assert.equal(context.window?.leak,undefined);
  insights = insights.map(item => item.id === 'pending' ? {...item,eligible:false} : item); await intervals[0](); await settle(); assert.equal(nodes.get('insight-analyze').hidden,true); assert.match(nodes.get('insight-summary').textContent,/环境与授权记录/);
  account = {role:'viewer'}; await intervals[0](); await settle(); assert.equal(nodes.get('insight-analyze').hidden,true); assert.equal(nodes.get('insight-automation-form').hidden,true);
  await nodes.get('insight-back').fire('click'); nodes.get('insight-source').value = 'codex'; await nodes.get('insight-source').fire('change');
  assert.match(nodes.get('insight-rows').textContent,/SSH/); assert.doesNotMatch(nodes.get('insight-rows').textContent,/通过天气服务|新的开发/);
  nodes.get('insight-search').value = 'nonexistent'; await nodes.get('insight-search').fire('input'); assert.equal(nodes.get('insight-empty').hidden,false);
  nodes.get('insight-search').value = ''; nodes.get('insight-source').value = 'all'; await nodes.get('insight-source').fire('change');
  extraItems = [0,1,2].map(index => ({...insights[2],id:'older-' + index,sessionId:'older-session-' + index,title:'历史任务 ' + index,sourceTime:'2026-09-01T01:00:00Z'}));
  await nodes.get('insight-refresh').fire('click'); await settle(); assert.equal(nodes.get('insight-load-more').hidden,false);
  await nodes.get('insight-load-more').fire('click'); await settle(); assert.match(nodes.get('insight-rows').textContent,/历史任务 2/); assert.equal(nodes.get('insight-load-more').hidden,true);
  await intervals[0](); await settle(); assert.match(nodes.get('insight-rows').textContent,/历史任务 2/,'background first page does not drop loaded historical pages');
  failingDetail = 'weather'; await openById('weather'); assert.match(nodes.get('insight-summary').textContent,/未能读取/); assert.doesNotMatch(nodes.get('insight-summary').textContent,/SSH/);
  assert.equal(nodes.get('insight-error').hidden,false);
  assert.match(html,/insight_live\.js/); assert.match(html,/data-product-ui/); assert.match(html,/\/session-insights\/archive\//);
  assert.doesNotMatch(html,/每天最多 3 份/); assert.doesNotMatch(controller,/maxDaily/); assert.match(html,/每日估算预算/); assert.match(html,/不代表提供方的精确账单/);
  assert.doesNotMatch(controller,/innerHTML|eval\(|new Function/);
  console.log('PASS real live controller: states, receipt/source times, current-vs-previous analysis titles, exact tool URL/result, step/evidence replay, no background redraw, revision-bound owner consent, budget-based automatic-source opt-in/update/off, environment-only eligibility, historical paging, guest read-only, filters, safe raw text, failed-detail recovery');
})().catch(error => {console.error(error);process.exitCode = 1;});
