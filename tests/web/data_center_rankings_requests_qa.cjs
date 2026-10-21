/* Execute the shipped ranking UI against an in-memory DOM and aggregate API. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../../agentpair/web_assets/data_center_rankings.js'),'utf8');
class Element {
  constructor(tag='div') {this.tagName=tag;this.children=[];this.listeners={};this.value='';this.textContent='';this.classList={toggle(){}};}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
  addEventListener(name,fn) {this.listeners[name]=fn;}
  setAttribute(name,value) {this[name]=String(value);}
  get selectedOptions() {return this.children.filter(child=>child.value===this.value);}
  focus() {}
}
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
async function open(search='') {
  const elements=new Map(),requests=[],counts={tools:60,skills:40,mcps:1};
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  for(const [id,values] of Object.entries({'dr-device':['all'],'dr-application':['all','workbuddy','codex'],'dr-collector':['all','applens','sessionlens']})) {
    const select=get(id);select.value='all';
    select.children=values.map(value=>Object.assign(new Element('option'),{value,textContent:value}));
  }
  const context=vm.createContext({document:{getElementById:get,createElement:tag=>new Element(tag)},
    location:{search},history:{replaceState(){}},addEventListener(){},URLSearchParams,AbortController,Intl,
    setTimeout(){return 1;},clearTimeout(){},fetch:async url=>{
      if(url==='/api/session')return {ok:true,json:async()=>({})};
      const params=new URL(url,'https://fixture.invalid').searchParams;requests.push(params);
      const page=Number(params.get('page')),size=Number(params.get('pageSize'));
      const data={snapshot:'fixture-snapshot',totals:{...counts},hasMore:{},facets:{devices:[]},coverage:{}};
      for(const key of Object.keys(counts)) {
        data[key]=Array.from({length:Math.max(0,Math.min(size,counts[key]-(page-1)*size))},(_,i)=>({name:key+' '+i,activityCount:1}));
        data.hasMore[key]=counts[key]>page*size;
      }
      return {ok:true,json:async()=>data};
    }});
  vm.runInContext(source,context);await flush();
  return {get,requests,click:async id=>{get(id).listeners.click();await flush();},change:async(id,value)=>{get(id).value=value;get(id).listeners.change();await flush();}};
}
(async()=>{
  const ui=await open(),last=()=>ui.requests.at(-1);
  assert.equal(last().get('object'),'all');assert.equal(last().get('collector'),'all');
  assert.equal(last().get('pageSize'),'50');assert.equal(ui.get('dr-rows').children.length,50);
  assert.equal(ui.get('dr-next').disabled,false);assert.match(ui.get('dr-total').textContent,/60/);
  await ui.click('dr-next');
  assert.equal(last().get('object'),'all');assert.equal(last().get('page'),'2');
  assert.equal(last().get('snapshot'),'fixture-snapshot');assert.equal(ui.get('dr-rows').children.length,10);
  assert.equal(ui.get('dr-next').disabled,true);
  await ui.click('dr-tab-skill');
  assert.equal(last().get('object'),'all');assert.equal(last().get('page'),'1');assert.equal(last().has('snapshot'),false);
  assert.equal(ui.get('dr-rows').children.length,40);assert.match(ui.get('dr-total').textContent,/40/);
  assert.equal(ui.get('dr-next').disabled,true);
  const before=ui.requests.length;await ui.click('dr-next');assert.equal(ui.requests.length,before);
  await ui.click('dr-tab-mcp');assert.equal(ui.get('dr-rows').children.length,1);assert.match(ui.get('dr-total').textContent,/1/);
  assert.equal(ui.get('dr-next').disabled,true);
  for(const [query,key,value] of [['?collector=sessionlens','collector','sessionlens'],['?collector=applens','collector','applens'],['?application=codex','application','codex'],['?device=fixture-device','device','fixture-device'],['?after=2026-10-01T00%3A00%3A00Z','after',null]]) {
    const filtered=await open(query),params=filtered.requests.at(-1);
    assert.equal(params.get('object'),'tool');assert.ok(params.get(key));if(value)assert.equal(params.get(key),value);
    await filtered.click('dr-tab-skill');assert.equal(filtered.requests.at(-1).get('object'),'skill');
  }
  const selected=await open('?object=skill');assert.equal(selected.requests.at(-1).get('object'),'all');
  assert.equal(selected.get('dr-rows').children.length,40);assert.equal(selected.get('dr-next').disabled,true);
  await ui.change('dr-collector','sessionlens');
  assert.equal(last().get('object'),'mcp');assert.equal(last().get('collector'),'sessionlens');assert.equal(last().has('snapshot'),false);
  console.log('Ranking UI: exact default cache scope, category pagination, tab snapshots and explicit filters passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
