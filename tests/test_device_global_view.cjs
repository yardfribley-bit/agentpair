const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('agentpair/web_assets/devices.js','utf8');
class Node {
 constructor(tag='div',text=''){this.tagName=tag;this.textContent=text;this.children=[];this.value='';this.hidden=false;this.attributes={};}
 append(...n){this.children.push(...n)}replaceChildren(...n){this.children=n}setAttribute(k,v){this.attributes[k]=v}get firstChild(){return this.children[0]}
}
const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,new Node());return elements.get(id)};$('device-scope').value='all';
const calls=[];const context={$,node:(tag,text)=>new Node(tag,text),csrf:'',role:'viewer',username:'',devices:[],device:null,selected:null,notice:e=>{throw e},render(){},renderCurrentTask:async()=>{},invalidatePlan(){},api:async p=>{calls.push(p);if(p==='/api/session')return {role:'viewer',csrf:null};if(p==='/api/audit/devices')return {items:[{id:'mac',name:'Mac Intel',ownerAccount:'admin',os:'macOS',collectors:[{id:'sessionlens',name:'SessionLens',records:5}]}]};throw Error('Unexpected '+p)}};
vm.createContext(context);vm.runInContext(source.slice(source.indexOf('async function refresh('),source.indexOf('function detailRow(')),context);
vm.runInContext(source.slice(source.indexOf('function renderCollectors('),source.indexOf("$('application-picker').onchange=")),context);
context.render=()=>{$('device-details').replaceChildren();context.renderPicker();};
(async()=>{
 await context.refresh();assert.equal(context.devices.length,1);assert.equal(context.devices[0].collectors[0].name,'SessionLens');assert.equal(context.devices[0].canManage,false);assert(!calls.includes('/api/devices'));
 const mac=context.devices[0],windows={id:'windows',name:'Windows office',ownerAccount:'other',snapshot:{os:'Windows'},collectors:[{id:'applens',name:'AppLens',records:3}]};
 context.devices=[mac,windows];context.device=windows;context.render();
 assert($('device-details').children.some(c=>c.href?.includes('device=windows')));
 $('device-query').value='Mac Intel';context.renderDeviceList();
 assert.equal(context.device.id,'mac');assert.equal($('device-list').children[0].attributes['aria-pressed'],'true');
 assert($('device-details').children.some(c=>c.href?.includes('device=mac')));
 assert(!$('device-details').children.some(c=>c.href?.includes('device=windows')));
 const collector=$('collector-status').children.find(c=>c.className==='collector-card');assert(collector.children.some(c=>c.href?.includes('device=mac')));
 $('device-query').value='does not exist';context.renderDeviceList();assert.equal(context.device,null);assert.equal($('device-details').children.length,0);assert.equal($('collector-status').hidden,true);assert.equal($('application-picker').disabled,true);
 assert.equal($('device-list').children[0].textContent,'没有匹配的设备。请调整范围或搜索词。');
 $('device-query').value='';$('device-scope').value='mine';context.renderDeviceList();assert.equal(context.device,null);assert.equal($('device-list').children[0].textContent,'没有匹配的设备。请调整范围或搜索词。');
 console.log('PASS anonymous visibility, ownership, filter selection, detail/collector links follow asset, zero results clear old data');
})().catch(e=>{console.error(e);process.exitCode=1});
