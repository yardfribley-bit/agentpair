const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('agentpair/web_assets/workspace.js','utf8');
class Node{constructor(tag,text){this.tag=tag;this.text=text;this.children=[];}append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}setAttribute(){} }
const context={pendingReply:{taskId:'t',baseRound:2,text:'我的问题'},dialogue:new Node('section'),deliverables:new Node('section'),el:(tag,text)=>new Node(tag,text)};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function renderPending('),source.indexOf('function processingText(')),context);
context.task={id:'t',messages:[]};vm.runInContext('renderPending(task)',context);
assert.equal(context.dialogue.children[0].children[1].text,'我的问题');
assert.equal(context.deliverables.children[1].text,'正在发送你的问题…');
context.task.messages=[{role:'user',round:3,text:'我的问题'}];vm.runInContext('renderPending(task)',context);assert.equal(context.pendingReply,null);
context.pendingReply={taskId:'another',baseRound:2,text:'另一个问题'};context.dialogue.replaceChildren();vm.runInContext('renderPending(task)',context);assert.equal(context.dialogue.children.length,0);
vm.runInContext(source.slice(source.indexOf('function processingText('),source.indexOf('function readableAnswer(')),context);
context.task={status:'running',round:3,events:[{kind:'stage_started',round:3,stage:'review'}]};assert.equal(vm.runInContext('processingText(task)',context),'正在核对结果，马上回复…');
console.log('Reply feedback checks passed');
