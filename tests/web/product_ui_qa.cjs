/** Regression QA for the approved Web composition. Synthetic DOM; no network. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('agentpair/web_assets/workspace.js','utf8');
class Element {
 constructor(tag,text,cls){this.tagName=tag.toUpperCase();this.children=[];this._text=text??'';this.className=cls||'';this.hidden=false;this.attributes={};this.classList={toggle:(n,on)=>{this.hidden=n==='hidden'?on:this.hidden},add:()=>{},remove:()=>{}};}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this.children=nodes;this._text=''}setAttribute(k,v){this.attributes[k]=v}
}
const context={el:(tag,text,cls)=>new Element(tag,text,cls),deliverables:new Element('section'),dialogue:new Element('section'),historyPanel:new Element('details'),lastVisibleRound:'',followLatest:true,awaitingVisibleAnswer:false,admin:false,
 showLatest(){},renderCloudAction(){},processingText:()=> '正在处理当前问题',readableAnswer:t=>t,URL,Blob,setTimeout,window:{},Date};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function renderDialogue('),source.indexOf('function renderCloudAction(')),context);
const task={id:'qa',round:2,status:'completed',messages:[{role:'user',round:1,text:'以前的问题'},{role:'navigator',stage:'review',round:1,answer:{finalAnswer:'以前的答案'}},{role:'user',round:2,text:'最近一次上报是什么时候？'},{role:'navigator',stage:'review',round:2,answer:{finalAnswer:'SessionLens 最近一次成功接收：10:42。'}}],platformResult:{round:2,action:'session_status',items:[],links:[],nextSteps:[],basis:'平台成功接收记录'}};
context.renderDeliverables(task);
assert.match(context.deliverables.textContent,/最近一次上报是什么时候/);
assert.match(context.deliverables.textContent,/最近一次成功接收：10:42/);
assert.doesNotMatch(context.deliverables.textContent,/以前的答案|涉及对象|下一步|交付位置|下载结果说明/);
assert.match(context.dialogue.textContent,/以前的问题.*以前的答案/);
assert.doesNotMatch(context.dialogue.textContent,/最近一次上报/);
const waiting={...task,round:3,status:'running',messages:[...task.messages,{role:'user',round:3,text:'上海天气用了哪些工具？'}]};
context.renderDeliverables(waiting);
assert.match(context.deliverables.textContent,/上海天气用了哪些工具/);assert.match(context.deliverables.textContent,/正在处理当前问题/);
assert.doesNotMatch(context.deliverables.textContent,/最近一次成功接收：10:42/);
const list=new Element('section');context.renderPlatformResult(list,{items:[{id:'d1',deviceName:'办公电脑',source:'SessionLens'}],nextSteps:['查看设备'],basis:'成功接收回执',links:[]});assert.match(list.textContent,/涉及对象.*办公电脑.*查询依据.*成功接收回执.*下一步/);
for(const name of ['workspace','devices','model_data','model_security','cloud_machines','packages','software']){
 const html=fs.readFileSync('agentpair/web_assets/'+name+'.html','utf8');assert.match(html,/<body class="agentpair-web">/);assert.match(html,/data-product-ui/);assert.match(html,/product_ui.js/);
}
const security=fs.readFileSync('agentpair/web_assets/model_security.html','utf8');assert.match(security,/<title>交互安全审计 · AgentPair/);assert.doesNotMatch(security,/<a class="brand" href="\/">AppLens/);
console.log('PASS current question and answer, collapsed history, no stale answer, no empty objects, shared Web shell (7 pages)');
