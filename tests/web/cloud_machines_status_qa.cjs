const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
async function scenario(state) {
 const elements = new Map();
 function element(){return {value:'',textContent:'',disabled:false,children:[],append(...items){this.children.push(...items);if(!this.value&&items[0]?.value)this.value=items[0].value;},replaceChildren(){this.children=[];this.value='';}};}
 const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
 const storage=new Map([['cloud-install-operation','a'.repeat(24)]]);
 let timer;
 const machine={id:'b'.repeat(24),name:'测试 Windows',platform:'windows',state:'released',expiresAt:'2020-01-01'};
 const responses={
 '/api/session':{role:'admin',csrf:'token'},'/api/software':{items:[]},'/api/cloud/machines':{items:[machine]},
 ['/api/cloud/operations/'+'a'.repeat(24)]:{state,stage:'verify',summary:'请核验',machineName:machine.name,softwareName:'输入法',evidence:{verified:false}}
 };
 vm.runInNewContext(fs.readFileSync('agentpair/web_assets/cloud_machines.js','utf8'),{
 document:{getElementById:get,createElement:element},Date,JSON,Error,Number,Math,
 sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
 fetch:async path=>({ok:true,json:async()=>responses[path]}),setInterval:fn=>{timer=fn;},confirm:()=>false
 });
 await new Promise(resolve=>setImmediate(resolve));
 assert.match(get('operation-target').textContent,/测试 Windows/);
 assert.match(get('function-status').textContent,/尚未验证/);
 assert.equal(get('install-machine').children.length,0);
 const card=get('machines').children[0];
 assert.equal(card.children.filter(c=>c.disabled).length,3);
 if(state==='verification_required'){
  assert.match(get('install-status').textContent,/版本待核验/);
  assert.equal(get('install').disabled,true);
 }else assert.match(get('install-status').textContent,/安装与版本验证通过/);
 assert.equal(storage.size,0);
}
(async()=>{await scenario('verification_required');await scenario('completed');console.log('Cloud machine status UI checks passed');})().catch(e=>{console.error(e);process.exitCode=1;});
