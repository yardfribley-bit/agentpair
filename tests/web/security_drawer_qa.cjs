/** Security evidence drawer controls. Pure DOM/state test with no API. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('agentpair/web_assets/model_security.js','utf8'),nodes=new Map(),listeners={};
const $=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,reset(){this.wasReset=true},focus(){this.wasFocused=true},querySelectorAll(){return []}});return nodes.get(id)};
let stopped=0,loads=0;
const ctx={$: $,selected:null,detailOpen:false,text(){},render(){ $('workspace').hidden=!ctx.detailOpen;$('audit-backdrop').hidden=!ctx.detailOpen;},window:{stopAnalysisReplay:()=>stopped++},document:{body:{style:{overflow:'hidden'}},addEventListener:(name,fn)=>listeners[name]=fn},load:()=>loads++};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function selectFinding('),source.indexOf('function renderHierarchy(')),ctx);
vm.runInContext(source.slice(source.indexOf('function closeFinding('),source.indexOf("$('remediation').onsubmit=")),ctx);
ctx.selectFinding('real-finding-1');assert.equal(ctx.selected,'real-finding-1');assert.equal(ctx.detailOpen,true);assert.equal($('workspace').hidden,false);assert.equal($('audit-backdrop').hidden,false);assert($('close-finding').wasFocused);
listeners.keydown({key:'Escape',preventDefault(){}});assert.equal(ctx.detailOpen,false);assert.equal($('workspace').hidden,true);assert.equal($('audit-backdrop').hidden,true);assert.equal(ctx.document.body.style.overflow,'');assert.equal(stopped,1);assert($('refresh').wasFocused);
ctx.selectFinding('real-finding-2');$('device').onchange();assert.equal(ctx.detailOpen,false);assert.equal(loads,1);
console.log('PASS evidence drawer selection, Escape closes/restores focus, stops replay, device change clears open detail');
