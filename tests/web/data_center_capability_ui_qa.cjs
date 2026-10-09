/** Actual search and independent rankings controllers, synthetic DOM/API only. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const read = file => fs.readFileSync('agentpair/web_assets/' + file,'utf8');
class Element {
  constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this.classes=new Set();this.hidden=false;this.disabled=false;this.value='';this.style={};this._text='';this.dataset={};this.classList={toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key)};}
  set textContent(value) {this._text=String(value??'');this.children=[];}
  get textContent() {return this._text+this.children.map(n=>n.textContent).join('');}
  set className(value) {this.classes=new Set(value.split(' '));}
  get selectedOptions() {return this.children.filter(n=>n.value===this.value);}
  append(...nodes) {for(const n of nodes) {n.parentElement=this;this.children.push(n);}}
  replaceChildren(...nodes) {this._text='';this.children=[];this.append(...nodes);}
  setAttribute(key,value) {this.attributes[key]=String(value);}
  addEventListener(key,fn) {(this.listeners[key] ||= []).push(fn);}
  async fire(key,extra={}) {for(const fn of this.listeners[key]||[]) fn({target:this,preventDefault(){},stopPropagation(){},...extra});}
  focus() {this.focused=true;}
  scrollIntoView() {}
  getClientRects() {return this.hidden?[]:[{}];}
  querySelectorAll() {return find(this,n=>['BUTTON','A','SUMMARY'].includes(n.tagName));}
}
const find=(node,predicate)=>node.children.flatMap(n=>[...(predicate(n)?[n]:[]),...find(n,predicate)]);
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const settle=async()=>{for(let i=0;i<8;i++) await new Promise(r=>setImmediate(r));};
function harness(html,js,search,handler,memory=new Map()) {
  const nodes=new Map(),requests=[],browserListeners={},timers=new Map();let timerId=0;
  for(const match of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {const node=new Element(match[1]);node.hidden=/\bhidden\b/.test(match[0]);node.disabled=/\bdisabled\b/.test(match[0]);nodes.set(match[2],node);}
  for(const match of html.matchAll(/<select id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {const node=nodes.get(match[1]);for(const option of match[2].matchAll(/<option value="([^"]+)"[^>]*>(.*?)<\/option>/g)) {const n=new Element('option');n.value=option[1];n.textContent=option[2];node.append(n);} node.value=node.children[0].value;}
  const $=id=>nodes.get(id),body=new Element('body'), context={
    document:{getElementById:$,createElement:tag=>new Element(tag),createTextNode:value=>{const n=new Element('#text');n.textContent=value;return n;},body,querySelector:()=>null,addEventListener(){}},
    location:{origin:'http://localhost',search},history:{replaceState(a,b,url){context.lastUrl=url;}},
    sessionStorage:{getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value),removeItem:key=>memory.delete(key)},
    fetch:async(url,options)=>{requests.push({url,options});return handler(url,options);},
    setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
    addEventListener:(key,fn)=>browserListeners[key]=fn,AbortController,URL,URLSearchParams,Intl,Date,Number,JSON,Object,String,Set,Map,Array,console};
  vm.createContext(context);vm.runInContext(js,context);return {$,nodes,requests,context,browserListeners,timers,body};
}
const rankHtml=read('data_center_rankings.html'),rankJs=read('data_center_rankings.js');
const searchHtml=read('data_center.html'),searchJs=read('data_center.js');
const scope='?object=mcp&device=dev-one&application=codex&collector=sessionlens&after=2026-10-01T00%3A00%3A00Z&before=2026-10-08T00%3A00%3A00Z';
const common={deviceCount:2,accountCount:1,sourceSessionCount:3,lastSeen:1791400000};
const mcp={...common,name:'doc-mcp',activityCount:12,callCount:12,directCount:9,wrappedCount:3,searchQuery:'mcp="doc-mcp"',methods:[{name:'create_with_markdown',callCount:8,searchQuery:'mcp="doc-mcp" && mcp_method="create_with_markdown"'}],methodsHasMore:true};
const skill={...common,name:'youtube-research-cn',activityCount:5,loadCount:0,readCount:5,searchQuery:'skill="youtube-research-cn"'};
const tool={...common,name:'Bash',activityCount:18,callCount:18,searchQuery:'tool="Bash"'};
let late=null,fail=false,empty=false,timeout=false;
const ranks=harness(rankHtml,rankJs,scope,async(url,options)=>{
  if(url==='/api/session') return response({});
  assert.ok(url.startsWith('/api/data-center/capabilities?'),'rankings reads only aggregate endpoint');
  if(timeout) return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(Error('aborted'),{name:'AbortError'})),{once:true}));
  if(fail) return response({error:'索引暂不可用'},503);
  const p=new URL(url,'http://localhost').searchParams;
  if(p.get('object')==='skill'&&late) return late.promise;
  const key={tool:'tools',skill:'skills',mcp:'mcps'}[p.get('object')], row={tool,skill,mcp}[p.get('object')];
  return response({[key]:empty?[]:[row],totals:{[key]:empty?0:101},hasMore:{[key]:!empty&&p.get('page')!=='2'},snapshot:p.get('snapshot')||'rank-snapshot',facets:{devices:[{id:'dev-one',name:'研发电脑'}]},coverage:{retainedRecords:90,searchedRecords:80,limitations:['仅覆盖已上报记录']}});
});
(async()=>{
  const $=ranks.$;
  await settle();assert.equal($('dr-tab-mcp').attributes['aria-selected'],'true');assert.match($('dr-title').textContent,/MCP使用排行/);assert.match($('dr-rows').textContent,/doc-mcp.*create_with_markdown.*12.*直接 9 · 命令包装 3/s);assert.match($('dr-scope').textContent,/研发电脑.*Codex.*SessionLens/);assert.match($('dr-total').textContent,/101 种/);assert.match($('dr-coverage-list').textContent,/仅覆盖已上报/);
  assert.ok(ranks.requests.every(r=>r.options.method==='GET'),'rankings never invokes model or POST');
  const nameLink=find($('dr-rows'),n=>n.tagName==='A')[0],drill=new URL(nameLink.href,'http://localhost');
  assert.equal(drill.pathname,'/model-data');assert.equal(drill.searchParams.get('q'),'mcp="doc-mcp"');assert.equal(drill.searchParams.get('object'),'mcp');for(const key of ['device','application','collector','after','before']) assert.equal(drill.searchParams.get(key),new URL(ranks.requests.at(-1).url,'http://localhost').searchParams.get(key),'drill preserves '+key);
  const methodLink=find($('dr-rows'),n=>n.tagName==='A')[1];assert.equal(new URL(methodLink.href,'http://localhost').searchParams.get('q'),'mcp="doc-mcp" && mcp_method="create_with_markdown"');assert.match($('dr-rows').textContent,/更多方法未在本页展开/);
  await $('dr-next').fire('click');await settle();assert.match(ranks.requests.at(-1).url,/page=2/);assert.match(ranks.requests.at(-1).url,/snapshot=rank-snapshot/);assert.equal($('dr-next').disabled,true);assert.equal($('dr-rows').children[0].children[0].textContent,'51');
  await $('dr-tab-skill').fire('click');await settle();assert.equal($('dr-count-heading').textContent,'证据记录');assert.match($('dr-rows').textContent,/仅有说明读取证据.*5.*加载 0 · 读取说明 5/s);assert.doesNotMatch($('dr-rows').textContent,/已启用/);assert.ok(!ranks.requests.at(-1).url.includes('snapshot='),'tab changes reset ranking snapshot');
  late=deferred();await $('dr-tab-skill').fire('click');await settle();await $('dr-tab-tool').fire('click');await settle();late.resolve(response({skills:[skill],totals:{skills:1}}));await settle();assert.match($('dr-rows').textContent,/Bash/);assert.doesNotMatch($('dr-rows').textContent,/youtube/);late=null;
  $('dr-device').value='all';await $('dr-device').fire('change');await settle();assert.match(ranks.requests.at(-1).url,/device=all/);assert.ok(!ranks.requests.at(-1).url.includes('snapshot='));
  fail=true;await $('dr-tab-mcp').fire('click');await settle();assert.equal($('dr-error').hidden,false);assert.match($('dr-error-text').textContent,/索引暂不可用.*范围已保留/);assert.equal($('dr-rows').children.length,0);assert.equal($('dr-empty').hidden,true);
  fail=false;await $('dr-retry').fire('click');await settle();assert.match($('dr-rows').textContent,/doc-mcp/);assert.equal($('dr-error').hidden,true);
  empty=true;await $('dr-tab-skill').fire('click');await settle();assert.equal($('dr-empty').hidden,false);assert.equal($('dr-total').textContent,'0 种已识别对象');assert.equal($('dr-rows').children.length,0);empty=false;
  const beforeInvalid=ranks.requests.length;$('dr-after').value='2026-10-09T00:00';$('dr-before').value='2026-10-01T00:00';await $('dr-apply-time').fire('click');await settle();assert.equal(ranks.requests.length,beforeInvalid);assert.match($('dr-error-text').textContent,/开始时间不能晚于结束时间/);
  $('dr-after').value='';$('dr-before').value='';timeout=true;await $('dr-retry').fire('click');await settle();const timer=[...ranks.timers.values()].find(t=>t.ms===30000);assert.ok(timer);timer.fn();await settle();assert.match($('dr-error-text').textContent,/读取超时/);assert.equal($('dr-progress').hidden,true);timeout=false;
  late=deferred();await $('dr-tab-skill').fire('click');await settle();await $('dr-cancel').fire('click');assert.match($('dr-error-text').textContent,/已停止本页等待/);late.resolve(response({skills:[skill],totals:{skills:1}}));await settle();assert.equal($('dr-rows').children.length,0);late=null;await $('dr-retry').fire('click');await settle();assert.match($('dr-rows').textContent,/youtube/);
  await $('dr-tab-skill').fire('keydown',{key:'ArrowRight'});await settle();assert.equal($('dr-tab-mcp').attributes['aria-selected'],'true');assert.equal($('dr-tab-mcp').focused,true);
  const memory=new Map([['agentpair.data-center.draft',JSON.stringify({query:'saved investigation',mode:'investigate',device:'old',object:'skill'})]]);
  const base={id:'dc-mcp',kind:'tool_call',tool:'Bash',function:'tdoc_call',command:'tdoc_call doc-mcp create_with_markdown',application:'codex',collector:'sessionlens',deviceId:'dev-one',deviceName:'研发电脑',timestamp:1791400000,capabilities:[{type:'mcp',name:'doc-mcp',method:'create_with_markdown',evidence:'wrapped',basis:'命令中明确服务与方法'},{type:'skill',name:'youtube-research-cn',evidence:'read',path:'/skills/youtube-research-cn/SKILL.md',basis:'读取说明文件'}]};
  const search=harness(searchHtml,searchJs,drill.search,async(url)=>{if(url==='/api/session')return response({});if(url.startsWith('/api/data-center/search?'))return response({items:[base],total:1,page:1,facets:{devices:[{id:'dev-one',name:'研发电脑'}]}});if(url.startsWith('/api/data-center/record?'))return response({item:{...base,arguments:{command:base.command},result:{ok:true},content:'完整已采内容',portrait:{userRequest:{association:'unavailable'},steps:[],neighbors:[]}}});throw Error('Unexpected API '+url);},memory);
  await settle();assert.equal(search.$('dc-object').value,'mcp');assert.equal(search.$('dc-query').value,'mcp="doc-mcp"');assert.match(search.requests.find(r=>r.url.startsWith('/api/data-center/search?')).url,/object=mcp/);assert.equal(search.$('dc-share-panel').hidden,true,'explicit ranking drill overrides saved investigation mode');assert.equal(search.requests.some(r=>r.url.startsWith('/api/data-center/capabilities')),false,'search does not load rankings');assert.ok(search.requests.every(r=>r.options.method==='GET'));assert.match(search.$('dc-results').textContent,/工具Bash.*MCP · doc-mcp.*命令包装调用.*create_with_markdown.*Skill · youtube-research-cn.*读取说明/s);
  await search.$('dc-results').children[0].fire('click');await settle();assert.match(search.$('dc-detail-capabilities').textContent,/MCP · doc-mcp.*命令包装调用.*读取说明.*命令中明确服务与方法/s);assert.match(search.$('dc-arguments').textContent,/tdoc_call doc-mcp/);assert.match(search.$('dc-return').textContent,/ok/);assert.match(search.$('dc-detail-facts').textContent,/上报机器研发电脑/s);
  search.$('dc-object').value='skill';await search.$('dc-object').fire('change');await settle();assert.match(search.requests.at(-1).url,/object=skill/);assert.equal(search.$('dc-query').value,'mcp="doc-mcp"','object change retains query text');assert.match(search.$('dc-rankings-link').href,/object=skill/);
  for(const script of [rankJs,searchJs]) assert.doesNotMatch(script,/innerHTML\s*=/);assert.ok(rankHtml.includes('源会话数不等于完整任务数'));assert.ok(searchHtml.includes('/model-data/rankings'));assert.ok(!searchHtml.includes('dr-table-section'),'rankings is a standalone page');
  search.browserListeners.pagehide();ranks.browserListeners.pagehide();
  console.log('PASS: Standalone tool/Skill/MCP ranking, scope and method drill, snapshot paging, read-only calls, empty/error/timeout/cancel/stale states, keyboard tabs, and evidence labels. Synthetic DOM/API only.');
})().catch(error=>{ranks.browserListeners.pagehide?.();console.error(error);process.exitCode=1;});
