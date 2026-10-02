// DOM unit test, not a substitute for real-browser visual acceptance.
const vm=require('vm'),fs=require('fs'),assert=require('assert');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.dataset={};this.classList={toggle(){}};}
 append(...xs){this.children.push(...xs);if(this.tag==='select'&&!this.value)this.value=xs[0]?.value||'';}
 replaceChildren(...xs){this.children=[];this.value=this.tag==='select'?'':this.value;this.append(...xs);}
 after(){}
 scrollIntoView(){} click(){this.clicked=true;}
}
const ids=['source','capture-layer','destination','identity','device','session','request','model','size','truncate','completeness','receipt','coverage','rows','empty','detail-title','original','basis-text','basis','position','raw','detail-tabs','view-tabs','search','text-search','status','match-status','copy','export','find'];
const nodes=Object.fromEntries(ids.map(id=>[id,new Element(['device','session','request'].includes(id)?'select':'div')]));
for(const tab of ['original','basis','full']){let e=new Element();e.dataset.tab=tab;nodes['detail-tabs'].append(e);}
for(const view of ['categories','full']){let e=new Element();e.dataset.view=view;nodes['view-tabs'].append(e);}
const calls=[{id:'one',sessionId:'s1',sessionName:'one',timestamp:1,body:'first raw',bodyBytes:9},{id:'two',sessionId:'s2',sessionName:'two',timestamp:2,body:'second raw',bodyBytes:10}];
const items=calls.map(c=>({id:c.id+'item',requestId:c.id,name:'USER.md',category:'用户资料',source:'USER.md',rawContent:c.body,bodyBytes:c.bodyBytes,classificationBasis:'标题',messageIndex:0,blockIndex:0,charStart:0,charEnd:c.body.length}));
nodes.identity.parentElement=new Element();
let copied=null;
const context={document:{getElementById:id=>nodes[id],createElement:t=>new Element(t),createTextNode:s=>({textContent:s}),hidden:false},location:{search:''},URL,URLSearchParams,Blob,AbortController,setTimeout,clearTimeout,setInterval(){},navigator:{clipboard:{writeText:async x=>copied=x}},fetch:async p=>({ok:true,json:async()=>p==='/api/session'?{username:'admin',csrf:'yes'}:p==='/api/devices'?{items:[{id:'device',name:'Mac'}]}:{device:{name:'Mac'},calls:p.includes('request=')?calls.filter(c=>c.id===new URL(p,'https://test').searchParams.get('request')):calls,items:p.includes('summary=1')?[]:items.filter(i=>i.requestId===new URL(p,'https://test').searchParams.get('request'))}})};
vm.runInNewContext(fs.readFileSync('agentpair/web_assets/model_data.js','utf8'),context);
(async()=>{await new Promise(r=>setTimeout(r,20));assert.equal(nodes.rows.children.length,1);assert.equal(nodes.original.textContent,'first raw');nodes.request.value='two';await nodes.request.onchange();assert.equal(nodes.original.textContent,'second raw');await nodes.copy.onclick();assert.equal(copied,'second raw');nodes.session.value='s1';await nodes.session.onchange();assert.equal(nodes.request.value,'one');assert.equal(nodes.original.textContent,'first raw');nodes.search.value='nomatch';nodes.search.oninput();assert.equal(nodes.rows.children.length,0);nodes['text-search'].value='raw';nodes.find.onclick();assert.equal(nodes['match-status'].textContent,'字符 6');console.log('PASS: device/session/call scope, raw reader, search, clipboard action; visual acceptance remains separate');})().catch(e=>{console.error(e);process.exit(1);});
