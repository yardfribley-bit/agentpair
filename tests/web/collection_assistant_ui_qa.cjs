/**
 * Functional QA for the shipped collection page. Run from the repository root:
 *   node tests/web/collection_assistant_ui_qa.cjs
 *
 * Executes the actual HTML/JS against a small deterministic DOM and an entirely
 * synthetic API. No network request, private session, or real model is used.
 * This verifies interactions and state; it does not certify browser/CSS layout.
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'agentpair/web_assets/model_data.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'agentpair/web_assets/model_data.js'), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toLowerCase();
    this.children = [];
    this.parentElement = null;
    this.attributes = {};
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this._text = '';
    this._value = '';
    this.selectedIndex = -1;
    this.classes = new Set();
    this.classList = {
      toggle: (name, force) => {
        const enabled = force === undefined ? !this.classes.has(name) : force;
        if (enabled) this.classes.add(name); else this.classes.delete(name);
      },
    };
  }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set value(value) {
    this._value = String(value ?? '');
    if (this.tagName === 'select') this.selectedIndex = this.children.findIndex(child => child.value === this._value);
  }
  get value() {
    return this.tagName === 'select' ? this.children[this.selectedIndex]?.value ?? '' : this._value;
  }
  get selectedOptions() { return this.selectedIndex >= 0 ? [this.children[this.selectedIndex]] : []; }
  append(...children) {
    for (let child of children) {
      if (typeof child !== 'object') child = Object.assign(new Element('#text'), {textContent: child});
      child.parentElement = this;
      this.children.push(child);
    }
    if (this.tagName === 'select' && this.selectedIndex < 0 && this.children.length) this.selectedIndex = 0;
  }
  replaceChildren(...children) { this.children = []; this._text = ''; this.selectedIndex = -1; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  after(...nodes) { if (this.parentElement) this.parentElement.append(...nodes); }
  scrollIntoView() { this.scrolled = true; }
  closest(tag) { return this.tagName === tag ? this : this.parentElement?.closest(tag) ?? null; }
}

function descendants(element, predicate) {
  return element.children.flatMap(child => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}
function rawRecord(id, collector = 'sessionlens', text = 'Synthetic source evidence') {
  const body = JSON.stringify({content: text});
  const call = {id, collector, collectorName: collector === 'sessionlens' ? 'SessionLens' : 'AppLens',
    application: 'WorkBuddy', sessionId: 'session-test', sessionName: 'QA session',
    timestamp: 1791331200, modelEvidence: '用户提问', body, bodyBytes: body.length,
    recordStatus: 'parseable', bodySHA256: 'abc123'};
  const item = {id: id + ':content', requestId: id, name: '用户提问', category: '提问',
    source: 'Synthetic QA', rawContent: text, bodyBytes: text.length,
    classificationBasis: '源日志事件类型', messageIndex: 0, blockIndex: 0, charStart: 0, charEnd: text.length};
  return {calls: [call], items: [item]};
}
function completed(id, text = '任务先检索资料，再调用工具；结果未独立核验。') {
  return {id, status: 'completed', result: {
    question: 'synthetic', answer: {text, basis: 'recorded', evidenceRefs: ['E001']},
    candidates: [{taskId: 'task-test', title: '合成的登录需求', source: 'workbuddy', status: 'supported',
      turnsCount: 2, recordCount: 3, reason: '提问与修改要求语义相关',
      requirements: [{turnId: 'turn-test', recordId: 'sessionlens:historical', text: '实现登录', relation: 'request'}],
      stages: [{title: '查找资料', kind: 'tool_call', text: '执行资料检索工具', collector: 'sessionlens',
        basis: 'recorded', recordId: 'sessionlens:historical'}],
      contexts: [{recordId: 'context-test', status: 'ambiguous', reason: '缺少独立请求标识'}],
      gaps: ['未提供运行验收记录']}],
    evidence: [{ref: 'E001', recordId: 'sessionlens:historical', collector: 'sessionlens',
      kind: 'user_message', text: '实现登录', truncated: false}],
    coverage: {indexedRecords: 4, totalRecords: 4, indexComplete: true, selectedRecords: 3},
    gaps: ['没有实际模型请求编号，不能计算模型交互次数'],
  }};
}
function apiReply(data, status = 200) { return {data, status}; }
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }

function harness({search = '', storage = new Map(), loggedIn = true, summary, route} = {}) {
  const ids = new Map();
  for (const match of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const element = new Element(match[1]);
    element.id = match[3];
    element.hidden = /\bhidden\b/.test(match[2]);
    element.disabled = /\bdisabled\b/.test(match[2]);
    ids.set(element.id, element);
  }
  for (const match of html.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {
    for (const value of match[2].matchAll(/<option\b[^>]*value="([^"]*)"[^>]*>(.*?)<\/option>/g)) {
      ids.get(match[1]).append(Object.assign(new Element('option'), {value: value[1], textContent: value[2]}));
    }
  }
  const page = new Element('main');
  for (const element of ids.values()) page.append(element);
  const requests = [], timers = new Map(), intervals = new Map(), handlers = new Map();
  let timerId = 0;
  const $ = id => ids.get(id);
  for (const match of source.matchAll(/(?:\$\(|set\()'([^']+)'/g)) assert(ids.has(match[1]), 'Harness missing actual DOM ID ' + match[1]);
  const storageApi = {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)),
    removeItem: key => storage.delete(key), clear: () => storage.clear()};
  const defaultSummary = summary ?? rawRecord('sessionlens:recent');
  const context = {
    document: {getElementById: $, createElement: tag => new Element(tag),
      createTextNode: text => Object.assign(new Element('#text'), {textContent: text}), hidden: false},
    location: {search}, URLSearchParams, AbortController, Intl, Date, Error, Number, Map, Set, Object,
    Array, Promise, JSON, Blob, URL, sessionStorage: storageApi, localStorage: storageApi,
    navigator: {clipboard: {writeText: async () => {}}},
    setTimeout: (fn, ms) => { timers.set(++timerId, {fn, ms}); return timerId; },
    clearTimeout: id => timers.delete(id),
    setInterval: (fn, ms) => { intervals.set(++timerId, {fn, ms}); return timerId; },
    clearInterval: id => intervals.delete(id),
    window: {addEventListener: (event, fn) => handlers.set(event, fn)},
    fetch: async (url, options = {}) => {
      requests.push({url, options});
      let reply = route ? await route(url, options) : undefined;
      if (reply === undefined) {
        if (url === '/api/session') reply = apiReply(loggedIn ? {username: 'qa-user', csrf: 'qa-csrf', role: 'user'} : {username: null, csrf: null});
        else if (url === '/api/audit/devices' || url === '/api/devices') reply = apiReply({items: [
          {id: 'device-A', name: 'QA A', lastActivity: 2}, {id: 'device-B', name: 'QA B', lastActivity: 1}]});
        else if (url.startsWith('/api/audit/model-data/') || url.startsWith('/api/devices/model-data/')) {
          const query = new URLSearchParams(url.split('?')[1]);
          if (query.get('summary')) reply = apiReply({calls: defaultSummary.calls, items: []});
          else if (query.has('q')) reply = apiReply({items: [{id: 'sessionlens:historical', collector: 'sessionlens',
            kind: '用户提问', source: 'workbuddy', timestamp: 1791331200, excerpt: '合成关键词：登录'}], coverage: '合成历史窗口'});
          else reply = apiReply(rawRecord(query.get('request'), 'sessionlens', 'Historical raw evidence'));
        } else if (url === '/api/collection/questions') reply = apiReply(completed('question-test'));
        else if (url.startsWith('/api/collection/questions/')) reply = apiReply(completed(url.split('/').at(-1)));
        else throw Error('Unexpected synthetic endpoint: ' + url);
      }
      return {ok: reply.status >= 200 && reply.status < 300, status: reply.status,
        json: async () => { if (reply.invalidJson) throw Error('Synthetic invalid JSON'); return reply.data; }};
    },
  };
  vm.runInNewContext(source, context, {filename: 'model_data.js'});
  const flush = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };
  return {$, requests, storage, page, handlers, intervals, flush,
    submit: async question => { $('collection-question').value = question; $('collection-question-form').onsubmit({preventDefault() {}}); await flush(); },
    change: async (id, value) => { $(id).value = value; $(id).onchange(); await flush(); },
    search: async query => { $('history-query').value = query; await $('history-search-form').onsubmit({preventDefault() {}}); await flush(); },
    click: async element => { await element.onclick(); await flush(); },
    fireTimer: async ms => { const item = [...timers].find(([, timer]) => timer.ms === ms); assert(item, 'Missing timer ' + ms); timers.delete(item[0]); await item[1].fn(); await flush(); },
  };
}

const tests = [];
const test = (name, run) => tests.push({name, run});
const postCount = h => h.requests.filter(request => request.url === '/api/collection/questions').length;

test('Successful answer retains question, semantic statuses, Chinese basis and evidence navigation', async () => {
  const h = harness(); await h.flush(); await h.submit('回顾登录需求');
  assert.equal(h.$('collection-question').value, '回顾登录需求');
  assert.equal(h.$('question-result').hidden, false);
  assert(h.$('question-candidates').textContent.includes('语义关联'));
  assert(h.$('question-candidates').textContent.includes('待确认'));
  assert(h.$('question-answer-basis').textContent.includes('采集原文'));
  assert.equal(h.$('question-gaps').hidden, false);
  assert.equal(h.$('question-gaps').open, false);
  await h.click(h.$('question-answer-refs').children[0]);
  assert.equal(h.$('question-evidence').open, true);
  assert.equal(h.$('question-evidence-list').children[0].scrolled, true);
  const link = descendants(h.$('question-evidence-list'), element => element.tagName === 'a')[0];
  const query = new URL(link.href, 'https://synthetic.invalid').searchParams;
  assert.equal(query.get('device'), 'device-A');
  assert.equal(query.get('request'), 'sessionlens:historical');
  assert.equal(query.get('collector'), 'sessionlens');
  assert.equal(query.get('scope'), 'global');
});

test('Original evidence deep-link hydrates an old record outside the recent summary window', async () => {
  const h = harness({search: '?device=device-A&collector=sessionlens&request=sessionlens%3Ahistorical&scope=global'});
  await h.flush();
  assert(h.requests.some(request => request.url.includes('request=sessionlens%3Ahistorical')), 'Historical ID was never hydrated');
  assert.equal(h.$('original').textContent, 'Historical raw evidence');
});

test('Original evidence deep-link hydrates even when the recent summary is empty', async () => {
  const h = harness({search: '?device=device-A&collector=sessionlens&request=sessionlens%3Ahistorical', summary: {calls: [], items: []}});
  await h.flush();
  assert.equal(h.$('original').textContent, 'Historical raw evidence');
});

test('Automatic refresh keeps a record selected while its summary request is pending', async () => {
  const gate = deferred(); let summaries = 0;
  const calls = [...rawRecord('old').calls, ...rawRecord('new').calls];
  const h = harness({summary: {calls, items: []}, route: url => {
    if (url.includes('summary=1')) return ++summaries === 1 ? apiReply({calls, items: []}) : gate.promise;
    if (url.includes('request=')) {
      const id = new URLSearchParams(url.split('?')[1]).get('request');
      return apiReply(rawRecord(id, 'sessionlens', id + ' raw'));
    }
  }});
  await h.flush();
  assert.equal(h.$('request').value, 'old');
  [...h.intervals.values()].find(timer => timer.ms === 60000).fn();
  await h.flush();
  await h.change('request', 'new');
  assert.equal(h.$('original').textContent, 'new raw');
  gate.resolve(apiReply({calls, items: []}));
  await h.flush();
  assert.equal(h.$('request').value, 'new');
  assert.equal(h.$('original').textContent, 'new raw');
});

test('Switching device clears results from the previous keyword-search scope', async () => {
  const h = harness(); await h.flush(); await h.search('登录');
  assert.equal(h.$('history-results').children.length, 1);
  await h.change('device', 'device-B');
  assert(h.$('search-scope').textContent.includes('device-B'));
  assert.equal(h.$('history-results').children.length, 0, 'Previous device hits remain actionable');
});

test('Switching collector clears keyword results which no longer match that collector', async () => {
  const h = harness(); await h.flush(); await h.search('登录');
  await h.change('collector', 'applens');
  assert.equal(h.$('history-results').children.length, 0, 'SessionLens hit remains under AppLens-only scope');
});

test('Failed raw-record reads leave an explicit reader error rather than an endless loading message', async () => {
  const h = harness({route: (url) => url.includes('request=') ? apiReply({error: 'Synthetic read denied'}, 403) : undefined});
  await h.flush();
  assert(h.$('original').textContent.includes('失败') || h.$('original').textContent.includes('denied'), 'Reader remains stuck on 正在读取');
});

test('Refreshing a running question restores its job without repeating a billable POST', async () => {
  const storage = new Map();
  const route = url => url === '/api/collection/questions' ? apiReply({id: 'pending-job', status: 'running', stage: '检索中'}) : undefined;
  const first = harness({storage, route}); await first.flush(); await first.submit('回顾登录需求');
  first.handlers.get('pagehide')();
  const reloaded = harness({storage, route}); await reloaded.flush();
  assert.equal(reloaded.$('collection-question').value, '回顾登录需求');
  if (!reloaded.requests.some(request => request.url.endsWith('/pending-job')) && !reloaded.$('question-error').hidden) await reloaded.click(reloaded.$('question-retry'));
  assert(reloaded.requests.some(request => request.url === '/api/collection/questions/pending-job'), 'Running job ID was lost on reload');
  assert.equal(postCount(reloaded), 0);
});

test('Stopping one question and submitting another cannot publish the earlier result', async () => {
  const old = deferred(); let posts = 0;
  const h = harness({route: url => {
    if (url !== '/api/collection/questions') return undefined;
    posts++; return posts === 1 ? old.promise : apiReply(completed('new-job', '第二个问题的回答'));
  }});
  await h.flush(); await h.submit('旧问题'); await h.click(h.$('question-cancel')); await h.submit('新问题');
  old.resolve(apiReply(completed('old-job', '陈旧的回答'))); await h.flush();
  assert.equal(h.$('question-answer-text').textContent, '第二个问题的回答');
  assert.equal(h.$('collection-question').value, '新问题');
});

test('Timeout resumption reads the existing job and does not create a second model request', async () => {
  const h = harness({route: url => url === '/api/collection/questions' ? apiReply({id: 'timeout-job', status: 'running', stage: '复核中'}) : undefined});
  await h.flush(); await h.submit('为什么失败'); await h.fireTimer(300000);
  assert.equal(h.$('question-retry').textContent, '重新查看结果');
  await h.click(h.$('question-retry'));
  assert.equal(postCount(h), 1);
  assert.equal(h.$('question-result').hidden, false);
});

test('A terminal missing-job response makes retry start a new query instead of looping on a missing ID', async () => {
  let posted = 0;
  const h = harness({route: url => {
    if (url === '/api/collection/questions') return apiReply(++posted === 1 ? {id: 'missing-job', status: 'running', stage: '检索中'} : completed('retry-job'));
    if (url.endsWith('/missing-job')) return apiReply({error: '问题不存在'}, 404);
  }});
  await h.flush(); await h.submit('为什么失败'); await h.fireTimer(2000);
  await h.click(h.$('question-retry'));
  assert.equal(postCount(h), 2, 'Retry only repeats the permanently missing job GET');
  assert.equal(h.$('question-result').hidden, false);
});

test('Model failure preserves question and permits a deliberate new request', async () => {
  let posted = 0;
  const h = harness({route: url => url === '/api/collection/questions' ? apiReply(++posted === 1 ? {id: 'failed-job', status: 'failed', error: '合成的模型失败'} : completed('retry-job')) : undefined});
  await h.flush(); await h.submit('原问题');
  assert.equal(h.$('question-error').hidden, false);
  assert.equal(h.$('collection-question').value, '原问题');
  await h.click(h.$('question-retry'));
  assert.equal(postCount(h), 2);
  assert.equal(h.$('question-result').hidden, false);
});

test('Followup includes only the previous completed job from the same device', async () => {
  const h = harness(); await h.flush(); await h.submit('第一个问题'); await h.submit('它为什么失败');
  let packet = JSON.parse(h.requests.filter(request => request.url === '/api/collection/questions').at(-1).options.body);
  assert.equal(packet.previousQuestionId, 'question-test');
  assert.equal(packet.scope, 'global');
  await h.change('device', 'device-B'); await h.submit('这台机器的任务');
  packet = JSON.parse(h.requests.filter(request => request.url === '/api/collection/questions').at(-1).options.body);
  assert.equal(packet.deviceId, 'device-B');
  assert(!packet.previousQuestionId);
});

test('Visitors can read evidence but cannot invoke the configured model', async () => {
  const h = harness({loggedIn: false}); await h.flush();
  assert.equal(h.$('question-submit').disabled, true);
  assert.equal(h.$('collection-question').disabled, true);
  await h.submit('一个问题');
  assert.equal(postCount(h), 0);
});

(async () => {
  let failed = 0;
  for (const {name, run} of tests) {
    try { await run(); console.log('PASS ' + name); }
    catch (error) { failed++; console.error('FAIL ' + name + '\n     ' + error.message); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} interactions passed; ${failed} failed. Synthetic DOM/API only.`);
  if (failed) process.exitCode = 1;
})();
