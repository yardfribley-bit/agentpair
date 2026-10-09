/** Actual controller, synthetic DOM/API only. Exercise search assistance as a user; no browser or model calls. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('agentpair/web_assets/data_center.html', 'utf8');
const js = fs.readFileSync('agentpair/web_assets/data_center.js', 'utf8');
const descendants = (node, predicate) => node.children.flatMap(child => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const response = data => ({ok:true,status:200,json:async () => data});
class Element {
  constructor(tag = 'div', doc = null) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = doc; this.children = []; this.listeners = {}; this.attributes = {}; this.classes = new Set(); this.dataset = {}; this.style = {};
    this.hidden = false; this.disabled = false; this.value = ''; this._text = ''; this.selectionStart = 0; this.selectionEnd = 0;
    this.classList = {toggle:(key, on) => on ? this.classes.add(key) : this.classes.delete(key),add:key => this.classes.add(key),remove:key => this.classes.delete(key),contains:key => this.classes.has(key)};
  }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set className(value) { this.classes = new Set(value.split(' ')); }
  get selectedOptions() { return this.children.filter(child => child.value === this.value); }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
  replaceChildren(...nodes) { this._text = ''; this.children = []; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
  contains(node) { return node === this || descendants(this, child => child === node).length > 0; }
  addEventListener(key, fn) { (this.listeners[key] ||= []).push(fn); }
  async fire(key, extra = {}) {
    const event = {target:this,currentTarget:this,defaultPrevented:false,preventDefault() {this.defaultPrevented = true;},stopPropagation() {this.cancelBubble = true;},...extra};
    for (const fn of this.listeners[key] || []) fn(event);
    return event;
  }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  scrollIntoView() {}
  getClientRects() { return this.hidden ? [] : [{}]; }
  querySelectorAll() { return descendants(this, node => ['BUTTON','A','SUMMARY'].includes(node.tagName)); }
}
const call = {id:'skill-read-one',recordId:'sessionlens:skill-read-one',kind:'tool_call',collector:'sessionlens',application:'codex',deviceId:'mac-one',deviceName:'研发 Mac',tool:'functions.exec',timestamp:1791400100,capabilities:[{type:'skill',name:'taste-skill',evidence:'read'}],activity:{kind:'skill_read',name:'taste-skill',title:'读取 taste-skill 说明',request:{text:'优化前端布局',recordId:'request-one',association:'candidate'},parameters:[{key:'path',label:'说明文件',value:'/design/taste-skill/SKILL.md'}],returnKind:'instructions',returnSummary:'取得说明文件。'}};
function harness() {
  const nodes = new Map(), requests = [], documentListeners = {}, browserListeners = {}, timers = new Map(); let timerId = 0;
  const doc = {activeElement:null,getElementById:id => nodes.get(id),createElement:tag => new Element(tag, doc),createTextNode:value => Object.assign(new Element('#text', doc), {textContent:value}),querySelector:() => null,addEventListener:(key, fn) => (documentListeners[key] ||= []).push(fn)};
  doc.body = new Element('body', doc);
  for (const match of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const node = new Element(match[1], doc); node.hidden = /\bhidden\b/.test(match[0]); node.id = match[2];
    for (const attr of match[0].matchAll(/\b(aria-[\w-]+|role)="([^"]*)"/g)) node.setAttribute(attr[1], attr[2]);
    nodes.set(match[2], node);
  }
  for (const match of html.matchAll(/<select id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {
    const node = nodes.get(match[1]);
    for (const option of match[2].matchAll(/<option value="([^"]+)"[^>]*>(.*?)<\/option>/g)) { const child = new Element('option', doc); child.value = option[1]; child.textContent = option[2]; node.append(child); }
    node.value = node.children[0]?.value || '';
  }
  const context = {document:doc,location:{origin:'http://localhost',pathname:'/model-data',search:''},sessionStorage:{getItem:() => null,setItem() {},removeItem() {}},setTimeout:(fn, ms) => {timers.set(++timerId, {fn, ms});return timerId;},clearTimeout:id => timers.delete(id),addEventListener:(key, fn) => browserListeners[key] = fn,AbortController,URL,URLSearchParams,Intl,Date,Number,JSON,Object,String,Set,Map,Array,console,fetch:async (url, options) => {
    requests.push({url,options});
    if (url === '/api/session') return response({});
    if (url.startsWith('/api/data-center/search?')) {
      const params = new URL(url, 'http://localhost').searchParams;
      const common = {page:1,pageSize:20,hasMore:false,snapshot:'stable-test-snapshot',facets:{devices:[{id:'mac-one',name:'研发 Mac'}]},coverage:{retainedRecords:1,searchedRecords:1}};
      return response(params.get('group') === 'request' ? {...common,group:'request',items:[],groups:[{id:'request-one',request:call.activity.request,recordCount:1,firstSeen:call.timestamp,lastSeen:call.timestamp,devices:[{id:'mac-one',name:'研发 Mac'}],applications:['codex'],items:[call],hasMore:false}],total:1,groupTotal:1,recordTotal:1,requestGroupTotal:1,unassignedRecordTotal:0} : {...common,items:[call],total:1});
    }
    if (url.startsWith('/api/data-center/record?')) return response({item:{...call,arguments:{path:'/design/taste-skill/SKILL.md'},result:{text:'Retained instructions'},content:{raw:'Retained record'}}});
    throw Error('Unexpected endpoint: ' + url);
  }};
  vm.runInNewContext(js, context);
  const $ = id => nodes.get(id);
  const input = async (value, caret = value.length, end = caret) => { $('dc-query').value = value; $('dc-query').setSelectionRange(caret, end); $('dc-query').focus(); await $('dc-query').fire('input'); await settle(); };
  const key = async (name, extra = {}) => { const event = await $('dc-query').fire('keydown', {key:name, ...extra}); await settle(); return event; };
  const options = () => descendants($('dc-completion-list'), node => node.tagName === 'BUTTON' && node.dataset.completionIndex !== undefined);
  const choose = async value => { const option = options().find(node => node.textContent.includes(value)); assert.ok(option, 'visible option for ' + value); await option.fire('click'); await settle(); };
  const searchRequests = () => requests.filter(request => request.url.startsWith('/api/data-center/search?'));
  return {$,requests,doc,documentListeners,browserListeners,input,key,options,choose,searchRequests};
}
async function verify() {
  const h = harness(); await settle();
  assert.equal(h.$('dc-completions').hidden, true, 'no distracting suggestions on untouched page');
  await h.input('kind=');
  assert.equal(h.$('dc-completions').hidden, false);
  assert.equal(h.options().length, 7, 'kind offers the seven kinds actually accepted by the search API');
  for (const [value, label] of [['user','用户'],['context','上下文'],['tool_call','工具调用'],['tool_result','工具返回'],['reasoning','思路'],['reply','回复'],['http','HTTP|模型请求']]) {
    const option = h.options().find(node => node.textContent.includes(value)); assert.ok(option, 'kind option ' + value); assert.match(option.textContent, new RegExp(label, 'i')); assert.ok(option.textContent.length > value.length + 6, 'option explains which retained data it finds');
  }
  assert.equal(h.$('dc-completion-list').getAttribute('role'), 'listbox');
  assert.ok(!h.options().some(option => /user_message|assistant_message|command_execution/.test(option.textContent)), 'source-specific kinds are not advertised as accepted canonical search values');
  assert.match(h.$('dc-query-guidance').textContent, /类型|种类|记录/);
  const beforeSelection = h.searchRequests().length;
  await h.choose('tool_call');
  assert.equal(h.$('dc-query').value, 'kind="tool_call"');
  assert.equal(h.searchRequests().length, beforeSelection, 'completion edits the query without executing it');
  assert.equal(h.$('dc-completions').hidden, true);
  // Editing a value in the middle must not consume the independent condition after the caret.
  const middle = 'app="codex" && kind=to || collector="sessionlens"';
  await h.input(middle, middle.indexOf('to ||') + 2); await h.choose('tool_result');
  assert.equal(h.$('dc-query').value, 'app="codex" && kind="tool_result" || collector="sessionlens"');
  assert.ok(h.$('dc-query').selectionStart >= 'app="codex" && kind="tool_result'.length && h.$('dc-query').selectionStart <= 'app="codex" && kind="tool_result"'.length, 'caret remains at the inserted value, before the untouched next condition');
  const quoted = 'kind="tool_c" && command="curl https://wttr.in/Shanghai"';
  await h.input(quoted, quoted.indexOf('tool_c') + 6); await h.choose('tool_call');
  assert.equal(h.$('dc-query').value, 'kind="tool_call" && command="curl https://wttr.in/Shanghai"', 'existing quote pair is reused');
  await h.input("kind='rea' || app!=\"workbuddy\"", 9); await h.choose('reasoning');
  assert.match(h.$('dc-query').value, /^kind=(?:'reasoning'|"reasoning") \|\| app!="workbuddy"$/, 'completion of a single-quoted draft remains valid and preserves the exclusion condition');
  await h.input('collector!='); await h.choose('applens'); assert.equal(h.$('dc-query').value, 'collector!="applens"', 'inequality operator remains unchanged');
  await h.input('KIND='); assert.equal(h.options().length, 7, 'field recognition accepts the same case-insensitive syntax as the API');
  const escapedPrefix = 'command="echo \\"kind=reply\\" || ignored" && kind=rea || app="codex"';
  await h.input(escapedPrefix, escapedPrefix.indexOf('kind=rea') + 8); await h.choose('reasoning');
  assert.equal(h.$('dc-query').value, 'command="echo \\"kind=reply\\" || ignored" && kind="reasoning" || app="codex"', 'escaped quotes and boolean-looking literal content are preserved when editing the later real condition');
  await h.input('tool="a || b && kind="'); assert.equal(h.$('dc-completions').hidden, true, 'operators and field-like text inside a quoted value do not become a new condition');
  await h.input('ki'); assert.ok(h.options().some(option => option.textContent.includes('kind')), 'field prefix offers a real field');
  await h.choose('kind'); assert.equal(h.$('dc-query').value, 'kind='); assert.equal(h.options().length, 7, 'field selection immediately teaches supported values');
  for (const [field, expected] of [['app',['codex','workbuddy']],['collector',['applens','sessionlens']],['location',['user','context','arguments','result','reply','metadata']]]) {
    await h.input(field + '='); assert.equal(h.options().length, expected.length, field + ' has exactly its supported enumerable values');
    for (const value of expected) assert.ok(h.options().some(option => option.textContent.includes(value)), field + ' option ' + value);
  }
  await h.input('command='); assert.equal(h.$('dc-completions').hidden, true, 'free-form command does not fabricate a dropdown of historical values'); assert.match(h.$('dc-query-guidance').textContent, /命令/);
  assert.equal(h.$('dc-query').value, 'command=', 'format guidance never silently fills a sample command');
  await h.input('kind=unsupported_value'); assert.equal(h.$('dc-completions').hidden, true); assert.match(h.$('dc-query-guidance').textContent, /未找到|支持/, 'unsupported kind teaches supported values rather than adding an invented kind');
  // Keyboard selection is local and the visual highlight follows Arrow keys.
  await h.input('kind='); await h.key('ArrowDown'); const activeFirst = h.$('dc-query').getAttribute('aria-activedescendant'); assert.ok(activeFirst, 'ArrowDown identifies the active option for assistive technology'); await h.key('ArrowUp'); assert.notEqual(h.$('dc-query').getAttribute('aria-activedescendant'), activeFirst, 'ArrowUp changes the selected suggestion');
  const enter = await h.key('Enter'); assert.equal(enter.defaultPrevented, true, 'Enter selects an open suggestion rather than submitting'); assert.equal(h.searchRequests().length, beforeSelection, 'keyboard completion also never sends a search'); assert.ok(h.$('dc-query').value.startsWith('kind="'));
  await h.input('kind='); await h.key('ArrowUp'); await h.key('Enter'); assert.equal(h.$('dc-query').value, 'kind="http"', 'initial ArrowUp selects the final item, without skipping it');
  await h.input('kind='); const beforeModifiedEnter = h.$('dc-query').value; const shiftEnter = await h.key('Enter', {shiftKey:true}); assert.equal(shiftEnter.defaultPrevented, false, 'Shift+Enter retains a newline rather than accepting a suggestion'); assert.equal(h.$('dc-query').value, beforeModifiedEnter);
  const shiftTab = await h.key('Tab', {shiftKey:true}); assert.equal(shiftTab.defaultPrevented, false, 'Shift+Tab retains backwards focus movement'); assert.equal(h.$('dc-query').value, beforeModifiedEnter, 'backwards focus movement never changes the query');
  await h.input('kind='); const escaped = await h.key('Escape'); assert.equal(h.$('dc-completions').hidden, true); assert.equal(h.$('dc-query').value, 'kind=', 'Escape leaves the draft intact');
  const closedEnter = await h.key('Enter'); assert.equal(closedEnter.defaultPrevented, false, 'plain Enter with no popup retains multiline textarea behavior');
  await h.input('kind='); await h.$('dc-query').fire('compositionstart'); await h.input('kind=用户'); assert.equal(h.$('dc-completions').hidden, true, 'IME composition suspends suggestions');
  const composingEnter = await h.key('Enter', {isComposing:true,keyCode:229}); assert.equal(h.searchRequests().length, beforeSelection, 'IME Enter never sends the draft'); assert.equal(h.$('dc-query').value, 'kind=用户');
  await h.$('dc-search-form').fire('submit'); await settle(); assert.equal(h.searchRequests().length, beforeSelection, 'a form event arriving during IME composition also cannot submit the incomplete draft');
  await h.$('dc-query').fire('compositionend'); await settle(); await h.input('kind='); assert.equal(h.options().length, 7, 'completion resumes after IME commit');
  const explicitBefore = h.searchRequests().length; await h.key('Enter', {ctrlKey:true}); assert.equal(h.searchRequests().length, explicitBefore + 1, 'Ctrl+Enter explicitly searches');
  await h.$('dc-mode-investigate').fire('click'); await h.input('kind='); assert.equal(h.$('dc-completions').hidden, true, 'natural-language investigation never displays structured-query candidates');
  assert.equal(h.$('dc-filter-panel').hidden, false, 'the required natural-language device selection is not concealed in a closed filter panel');
  assert.equal(h.$('dc-advanced-filters').open, true, 'the required device selector is visible in natural-language mode');
  assert.equal(h.$('dc-device').disabled, false, 'natural-language device selection remains usable');
  await h.$('dc-mode-search').fire('click'); await h.$('dc-add-filter').fire('click'); assert.equal(h.$('dc-filter-panel').hidden, false); assert.equal(h.$('dc-add-filter').getAttribute('aria-expanded'), 'true');
  const groups = h.$('dc-field-groups').children; assert.equal(groups.length, 4, 'field assistance is divided into four meaningful groups');
  const fields = descendants(h.$('dc-field-groups'), node => node.dataset.searchField !== undefined); assert.equal(fields.length, 17, 'all supported fields can be inserted from the filter entry');
  assert.deepEqual(fields.map(node => node.dataset.searchField).sort(), ['tool','function','executor','command','skill','mcp','mcp_method','ip','domain','target_ip','target_domain','device','session','app','collector','kind','location'].sort());
  await h.input('tool="Bash"'); const fieldRequests = h.searchRequests().length; await fields.find(node => node.dataset.searchField === 'kind').fire('click'); await settle(); assert.equal(h.$('dc-query').value, 'tool="Bash" && kind='); assert.equal(h.searchRequests().length, fieldRequests, 'adding a field does not surprise the user with a request'); assert.equal(h.options().length, 7);
  await h.$('dc-add-filter').fire('click'); const quotedDraft = 'tool="Bash" && command="echo hello"'; await h.input(quotedDraft, quotedDraft.indexOf('Bash') + 2); await fields.find(node => node.dataset.searchField === 'kind').fire('click'); await settle(); assert.equal(h.$('dc-query').value, quotedDraft + ' && kind=', 'adding a filter while the caret is inside an existing quoted value preserves that value and adds a separate condition'); assert.equal(h.options().length, 7); assert.equal(h.searchRequests().length, fieldRequests);
  // Existing request grouping and original evidence drawer remain accessible through the new entry.
  await h.input('skill="taste-skill"'); await h.$('dc-search-form').fire('submit'); await settle(); assert.match(h.$('dc-count').textContent, /1.*需求.*1.*记录/);
  let card = h.$('dc-results').children[0]; const buttons = descendants(card, node => node.tagName === 'BUTTON'); await buttons.find(button => button.textContent.startsWith('展开 ')).fire('click'); await buttons.find(button => button.textContent.includes('查看原文详情')).fire('click'); await settle(); assert.equal(h.$('dc-detail').hidden, false); assert.match(h.$('dc-detail-title').textContent, /taste-skill/); await h.$('dc-close-detail').fire('click');
  assert.equal(h.$('dc-filter-panel').hidden, true, 'the picker yields focus back to query input after adding a field'); await h.$('dc-add-filter').fire('click'); assert.equal(h.$('dc-filter-panel').hidden, false); await h.$('dc-add-filter').fire('click'); assert.equal(h.$('dc-filter-panel').hidden, true, 'filter assistance can be opened and dismissed repeatedly');
  assert.ok(h.requests.every(request => request.options.method === 'GET'), 'read-only assistance never invokes a model or mutates records'); assert.doesNotMatch(js, /innerHTML\s*=/, 'completion inserts untrusted query as safe text'); h.browserListeners.pagehide();
  console.log('PASS: Supported field/value completion with readable meaning, caret-aware insertion, quoted values/operators, local keyboard selection, IME safety, natural-language separation, grouped field assistance and existing group/evidence navigation. Synthetic DOM/API only.');
}
verify().catch(error => {console.error(error);process.exitCode = 1;});
