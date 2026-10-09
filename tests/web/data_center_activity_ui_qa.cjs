/** Actual capability results controller. Synthetic fixtures, with optional read-only capture QA.
 * node tests/web/data_center_activity_ui_qa.cjs
 * node tests/web/data_center_activity_ui_qa.cjs --captured /private/tmp/captured.json
 * node tests/web/data_center_activity_ui_qa.cjs --serve 18964 (synthetic visual preview)
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const assets = path.resolve('agentpair/web_assets');
const html = fs.readFileSync(path.join(assets,'data_center.html'),'utf8');
const js = fs.readFileSync(path.join(assets,'data_center.js'),'utf8');
const common = {kind:'tool_call',kindLabel:'工具调用',collector:'sessionlens',application:'codex',deviceId:'qa-mac',deviceName:'合成验收 · 研发 Mac',ownerAccount:'synthetic',timestamp:1791400000,confirmation:'recorded'};
const instructions = '# Skill instructions\n' + 'Full browser documentation. '.repeat(300);
const loaded = {...common,id:'skill-loaded',tool:'Skill',function:'Skill',capabilities:[{type:'skill',name:'agent-browser',evidence:'loaded'}],summary:{action:'调用工具 · Skill',resultPreview:instructions,outcome:{status:'unknown',label:'已取得工具返回'}},activity:{kind:'skill_load',name:'agent-browser',title:'加载 agent-browser',parameters:[{key:'skill',label:'Skill',value:'agent-browser'}],request:{text:'在 Windows 11 安装测试代理工具和 WorkBuddy。',association:'candidate',basis:'inferred_source_time'},returnKind:'instructions',returnSummary:'已取得 agent-browser 的操作说明。',calls:[{type:'skill',name:'agent-browser',evidence:'loaded',arguments:{skill:'agent-browser'}}]}};
const confirmation = {...loaded,id:'confirmation',activity:{...loaded.activity,request:{text:'已经连接',association:'candidate'}}};
const skillRead = {...common,id:'skill-read',tool:'functions.exec',function:'exec',capabilities:[{type:'skill',name:'taste-skill',evidence:'read',path:'/custom/design/taste-skill/SKILL.md'}],activity:{kind:'skill_read',name:'taste-skill',title:'读取 taste-skill 说明',parameters:[{key:'path',label:'说明文件',value:'/custom/design/taste-skill/SKILL.md'}],request:{text:'优化测试数据中心的前端布局。',association:'recorded',basis:'recorded_source_parent'},returnKind:'missing',returnSummary:'未采集到可唯一对应的返回。',calls:[{type:'skill',name:'taste-skill',evidence:'read',arguments:{path:'/custom/design/taste-skill/SKILL.md'},sourceOffset:22}]}};
const nestedCalls = [{type:'mcp',name:'tinyfish',method:'run',evidence:'wrapped',arguments:{url:'https://example.test/catalog',goal:'读取公开测试产品列表'},outerCallId:'qa-wrapper',sourceOffset:8},{type:'mcp',name:'tinyfish',method:'extract',evidence:'wrapped',arguments:{url:'https://example.test/pricing',fields:['name','price']},outerCallId:'qa-wrapper',sourceOffset:96}];
const wrapped = {...common,id:'wrapped',tool:'functions.exec',function:'exec',command:'await tools.mcp__tinyfish__run(...); await tools.mcp__tinyfish__extract(...)',capabilities:[{type:'mcp',name:'tinyfish',method:'run',evidence:'wrapped'},{type:'mcp',name:'tinyfish',method:'extract',evidence:'wrapped'}],activity:{kind:'mcp_call',name:'tinyfish',method:'run',title:'调用 tinyfish · run 等 2 项',parameters:[{key:'url',label:'访问地址',value:'https://example.test/catalog'}],request:{text:'读取测试网站的产品与价格。',association:'recorded',basis:'recorded_source_parent'},returnKind:'wrapper_result',returnSummary:'记录到外层 exec 返回；2 项子调用的独立返回尚需核对。',calls:nestedCalls}};
const rows = [loaded,confirmation,skillRead,wrapped];
const action = {...common,id:'later-action',timestamp:1791400005,tool:'Bash',command:'agent-browser open https://example.test/',summary:{action:'访问测试网页',resultPreview:'已保留浏览命令返回'}};
const neighbor = {...action,id:'nearby-action',association:'same_session_neighbor',summary:{action:'附近其他动作',resultPreview:'不能认定属于相同需求'}};
const details = Object.fromEntries(rows.map(item => [item.id,{...item,arguments:item.id==='wrapped'?{code:item.command}:item.id==='skill-read'?{code:'text(await tools.exec_command({cmd:"cat /custom/design/taste-skill/SKILL.md"}))'}:{skill:'agent-browser'},result:item.id==='skill-read'?null:item.id==='wrapped'?{content:'outer-only-result-marker'}:{text:instructions},content:{fixture:item.id},portrait:{userRequest:{userInput:item.activity.request.text,association:item.activity.request.association},steps:[item,{...action,association:'recorded'},{...neighbor,association:'candidate'}],neighbors:[neighbor]},relations:[{from:item.id,to:action.id,relation:'source_parent',basis:'recorded_unique_parent_id'}]}]));

class Element {
  constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this.dataset={};this.classes=new Set();this.hidden=false;this.disabled=false;this.value='';this.style={};this._text='';this.classList={toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key)};}
  set textContent(value) {this._text=String(value??'');this.children=[];}
  get textContent() {return this._text+this.children.map(n=>n.textContent).join('');}
  set className(value) {this.classes=new Set(value.split(' '));}
  get selectedOptions() {return this.children.filter(n=>n.value===this.value);}
  append(...nodes) {for(const n of nodes) {n.parentElement=this;this.children.push(n);}}
  replaceChildren(...nodes) {this._text='';this.children=[];this.append(...nodes);}
  setAttribute(key,value) {this.attributes[key]=String(value);}
  addEventListener(key,fn) {(this.listeners[key] ||= []).push(fn);}
  async fire(key,extra={}) {for(const fn of this.listeners[key]||[]) fn({target:this,preventDefault(){},stopPropagation(){},...extra});}
  focus() {} scrollIntoView() {} getClientRects() {return this.hidden?[]:[{}];}
  querySelectorAll() {return find(this,n=>['BUTTON','A','SUMMARY'].includes(n.tagName));}
}
const find=(node,predicate)=>node.children.flatMap(n=>[...(predicate(n)?[n]:[]),...find(n,predicate)]);
const settle=async()=>{for(let i=0;i<8;i++) await new Promise(r=>setImmediate(r));};
function harness(list=rows,detailMap=details) {
  const nodes=new Map(),requests=[],timers=new Map();let timerId=0;
  for(const match of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {const n=new Element(match[1]);n.hidden=/\bhidden\b/.test(match[0]);nodes.set(match[2],n);}
  for(const match of html.matchAll(/<select id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {const n=nodes.get(match[1]);for(const option of match[2].matchAll(/<option value="([^"]+)"[^>]*>(.*?)<\/option>/g)) {const o=new Element('option');o.value=option[1];o.textContent=option[2];n.append(o);} n.value=n.children[0].value;}
  const $=id=>nodes.get(id),context={document:{getElementById:$,createElement:tag=>new Element(tag),createTextNode:value=>Object.assign(new Element('#text'),{textContent:value}),body:new Element('body'),querySelector:()=>null,addEventListener(){}},location:{origin:'http://localhost',search:''},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;},clearTimeout:id=>timers.delete(id),addEventListener(){},AbortController,URL,URLSearchParams,Intl,Date,Number,JSON,Object,String,Set,Map,Array,console,fetch:async(url,options)=>{requests.push({url,options});let data;if(url==='/api/session')data={};else if(url.startsWith('/api/data-center/search?'))data={items:list,total:list.length,page:1,facets:{devices:[{id:'qa-mac',name:'合成验收 · 研发 Mac'}]}};else if(url.startsWith('/api/data-center/record?'))data={item:detailMap[new URL(url,'http://localhost').searchParams.get('id')]};else throw Error('Unexpected endpoint '+url);return {ok:true,status:200,json:async()=>data};}};
  vm.runInNewContext(js,context);return {$,requests,open:async(index)=>{await $('dc-results').children[index].fire('click');await settle();}};
}
function visibleText(node) {if(node.hidden)return '';if(node.tagName==='DETAILS'&&!node.open)return node.children.find(n=>n.tagName==='SUMMARY')?.textContent||'';return node._text+node.children.map(visibleText).join('');}
async function verify() {
  const h=harness();await settle();const cards=h.$('dc-results').children;
  assert.match(cards[0].textContent,/加载 agent-browser.*上报机器：合成验收.*用户输入候选关联.*Windows 11.*说明返回已取得/s);
  assert.doesNotMatch(cards[0].textContent,/Full browser documentation|已执行浏览|调用工具 · Skill/);
  assert.doesNotMatch(cards[1].textContent,/已经连接/);assert.match(cards[1].textContent,/用户输入未关联.*未找到对应用户输入/);
  assert.match(cards[2].textContent,/读取 taste-skill 说明.*说明文件.*\/custom\/design\/taste-skill\/SKILL.md/s);
  await h.open(0);assert.equal(h.$('dc-detail-title').textContent,'加载 agent-browser');assert.match(h.$('dc-task-context').textContent,/Windows 11.*候选需求.*尚未确认/s);assert.equal(h.$('dc-detail-outcome').hidden,true);
  assert.doesNotMatch(visibleText(h.$('dc-return')),/Full browser documentation/);assert.match(visibleText(h.$('dc-return')),/实际使用它完成的动作需要后续调用证据/);
  assert.match(h.$('dc-related').textContent,/访问测试网页/);assert.doesNotMatch(h.$('dc-related').textContent,/附近其他动作/);assert.match(h.$('dc-neighbors').textContent,/附近其他动作.*归属未确认/s);
  await h.open(1);assert.match(h.$('dc-task-context').textContent,/无法单独还原完整需求.*已经连接/s);assert.doesNotMatch(visibleText(h.$('dc-task-context')),/已经连接/);
  await h.open(2);assert.match(h.$('dc-arguments').textContent,/\/custom\/design\/taste-skill\/SKILL.md.*源码位置：22/s);assert.match(h.$('dc-return').textContent,/未采集到可唯一对应/);assert.doesNotMatch(h.$('dc-return').textContent,/已取得说明|成功/);
  await h.open(3);assert.equal(h.$('dc-return-title').textContent,'外层工具的返回');assert.match(h.$('dc-arguments').textContent,/1\. 调用 tinyfish · run.*example.test\/catalog.*2\. 调用 tinyfish · extract.*example.test\/pricing.*展开外层/s);
  assert.match(h.$('dc-return').textContent,/尚未为各项子调用分别配对返回/);assert.doesNotMatch(visibleText(h.$('dc-return')),/outer-only-result-marker/);
  assert.ok(h.requests.every(r=>r.options.method==='GET'));assert.doesNotMatch(js,/innerHTML\s*=/);
  assert.ok(html.indexOf('id="dc-task-context"')<html.indexOf('id="dc-detail-summary"'),'detail starts with the request');
  console.log('PASS: Skill/MCP activity titles, request certainty, confirmation suppression, bounded instruction summaries, distinct wrapper parameters/returns, and confirmed followups. Synthetic DOM/API only.');
  const capturedIndex=process.argv.indexOf('--captured');
  if(capturedIndex>=0) {
    const captured=JSON.parse(fs.readFileSync(process.argv[capturedIndex+1],'utf8')),list=captured.items;
    assert.equal(list.length,3,'Expected the three captured agent-browser records');
    const detailMap=Object.fromEntries(list.map(item=>[item.id,{...item,...(item.detail||{}),result:item.result??item.detail?.result??item.detail?.returnPreview??null,taskContext:item.taskContext||item.detail?.taskContext}]));
    const real=harness(list,detailMap);await settle();
    for(let i=0;i<3;i++) {const card=real.$('dc-results').children[i];assert.match(card.textContent,/加载 agent-browser/);assert.doesNotMatch(visibleText(card),/Base directory|Use `agent-browser` CLI|已经连接/);await real.open(i);assert.match(real.$('dc-detail-title').textContent,/加载 agent-browser/);assert.doesNotMatch(visibleText(real.$('dc-return')),/Base directory|full browser automation/);if(list[i].activity?.request?.association==='candidate'&&list[i].activity.request.text!=='已经连接')assert.match(card.textContent,/候选关联/);}
    console.log('PASS: Three supplied captured agent-browser records show loading evidence without presenting documentation as browser execution or confirmation text as a complete request. Capture read only; no remote API.');
  }
}
if(process.argv.includes('--serve')) {
  const port=Number(process.argv[process.argv.indexOf('--serve')+1]||18964),http=require('node:http');
  http.createServer((req,res)=>{const u=new URL(req.url,'http://localhost');res.setHeader('Cache-Control','no-store');if(u.pathname==='/api/session'){res.setHeader('Content-Type','application/json');res.end('{}');return;}if(u.pathname.startsWith('/api/data-center/')){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(u.pathname.endsWith('/record')?{item:details[u.searchParams.get('id')]}:{items:rows,total:rows.length,page:1,facets:{devices:[{id:'qa-mac',name:'合成验收 · 研发 Mac'}]},coverage:{retainedRecords:4,searchedRecords:4,limitations:['本地合成界面验收记录']}}));return;}const file=u.pathname==='/model-data'||u.pathname==='/'?'data_center.html':path.basename(u.pathname);try{res.setHeader('Content-Type',file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':file.endsWith('.svg')?'image/svg+xml':'text/html');res.end(fs.readFileSync(path.join(assets,file)));}catch(_){res.statusCode=404;res.end('Not found');}}).listen(port,'127.0.0.1',()=>console.log('Synthetic capability preview http://127.0.0.1:'+port+'/model-data'));
} else verify().catch(error=>{console.error(error);process.exitCode=1;});
