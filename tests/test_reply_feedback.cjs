const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('agentpair/web_assets/workspace.js','utf8');
class Node{constructor(tag,text){this.tag=tag;this.text=text;this.children=[];}append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}setAttribute(){} }
const context={pendingReplies:new Map([['t',{taskId:'t',baseRound:2,text:'我的问题'}]]),dialogue:new Node('section'),deliverables:new Node('section'),el:(tag,text)=>new Node(tag,text)};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function renderPending('),source.indexOf('function processingText(')),context);
context.task={id:'t',messages:[]};vm.runInContext('renderPending(task)',context);
assert.equal(context.deliverables.children[0].children[1].text,'我的问题');
assert.equal(context.deliverables.children[2].text,'正在发送你的问题…');
context.task.messages=[{role:'user',round:3,text:'我的问题'}];vm.runInContext('renderPending(task)',context);assert.equal(context.pendingReplies.has('t'),false);
context.pendingReplies.set('another',{taskId:'another',baseRound:2,text:'另一个问题'});context.dialogue.replaceChildren();vm.runInContext('renderPending(task)',context);assert.equal(context.dialogue.children.length,0);
vm.runInContext(source.slice(source.indexOf('function processingText('),source.indexOf('function readableAnswer(')),context);
context.task={status:'running',round:3,events:[{kind:'stage_started',round:3,stage:'review'}]};assert.equal(vm.runInContext('processingText(task)',context),'正在核对结果，马上回复…');
console.log('Reply feedback checks passed');

(async()=>{
 const nodes={ 'reply-form':{},reply:{value:'新会话的问题'},send:{},connection:{},budget:{},'task-list':{replaceChildren(){}} };
 let posted=0;const x={current:'B',pendingReplies:new Map([['A',{taskId:'A',baseRound:1,text:'旧问题'}]]),window:{agentpairCurrentTask:{id:'B',round:1}},$:id=>nodes[id],error(){},render(){},showLatest(){},refresh:async()=>{},api:async()=>{posted++;return {id:'B'}},awaitingVisibleAnswer:false,followLatest:false};
 vm.createContext(x);vm.runInContext(source.slice(source.indexOf("$('reply-form').onsubmit="),source.indexOf("$('cancel').onclick=")),x);
 await nodes['reply-form'].onsubmit({preventDefault(){}});assert.equal(posted,1);assert(x.pendingReplies.has('A'));assert(!x.pendingReplies.has('B'));
 let release,calls=0;const r={logged:true,refreshInFlight:false,api:()=>{calls++;return new Promise(resolve=>release=resolve)},$:id=>nodes[id],current:null,csrf:'',admin:true,error(){},el(){},Date};vm.createContext(r);vm.runInContext(source.slice(source.indexOf('async function refresh('),source.indexOf("$('login-form').onsubmit=")),r);
 const first=r.refresh();await r.refresh();assert.equal(calls,1);release({budget:{estimatedReservedCNY:0,estimatedLimitCNY:null},items:[]});await first;assert.equal(r.refreshInFlight,false);
 const a={AbortController,setTimeout:fn=>setTimeout(fn,5),clearTimeout,fetch:(_,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(Error('abort'),{name:'AbortError'})))),csrf:''};vm.createContext(a);vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function details(')),a);await assert.rejects(a.api('/api/tasks'),/20 秒/);
 const button={};let created=0,resolveCreate;const fields={'execution-profile':{value:'none'},'engineering-method':{value:'pair'},title:{value:'QA'},goal:{value:'goal'},acceptance:{value:''},adapter:{value:'discussion'},target:{value:''},'create-form':{}};
 const creation={creatingTask:false,pendingCreate:null,$:id=>fields[id],Date,error(){},render(){},refresh:async()=>{},api:()=>{created++;return new Promise(resolve=>resolveCreate=resolve)}};vm.createContext(creation);vm.runInContext(source.slice(source.indexOf('async function checkCreatedTask('),source.indexOf("$('reply-form').onsubmit=")),creation);
 const event={preventDefault(){},target:{querySelector(){return button}}};const submit=fields['create-form'].onsubmit(event);await fields['create-form'].onsubmit(event);assert.equal(created,1);assert.equal(button.disabled,true);resolveCreate({id:'created'});await submit;assert.equal(creation.creatingTask,false);assert.equal(button.disabled,false);
 console.log('Cross-session, polling overlap, timeout and duplicate-create checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
