/** Actual replay code with synthetic DOM and responses; no network or real data. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');

class Node {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.value='';this.hidden=false;this.classList={add(){},toggle(){}};}
 append(...nodes){const empty=this.children.length===0;this.children.push(...nodes);if(this.tagName==='select'&&empty&&nodes[0])this.value=nodes[0].value;}
 replaceChildren(...nodes){this.children=[];if(this.tagName==='select')this.value='';this.append(...nodes);}
 setAttribute(){}
 querySelectorAll(){return [];}
}
const nodes=new Map();
const get=id=>{if(!nodes.has(id))nodes.set(id,new Node(id==='analysis-request'?'select':'div'));return nodes.get(id);};
const body=JSON.stringify({messages:[{role:'system',content:'credential fixture'}]});
const bodySHA256=crypto.createHash('sha256').update(body).digest('hex');
const evidence=id=>({requestId:id,bodySHA256,source:'workbuddy_network_context',timestamp:1700000000,pointer:'/messages/0/content',start:0,end:10,preview:' [凭据已隐藏] ',role:'system',task:'检查任务'});
const requests=[];
const context={console,TextEncoder,AbortSignal,crypto:crypto.webcrypto,URLSearchParams,$:get,analysisSelection:null,analysisFinding:null,query:new URLSearchParams(),window:{},
 document:{getElementById:get,createElement:tag=>new Node(tag),addEventListener(){}},
 fetch:async url=>{const id=new URL(url,'https://synthetic.invalid').searchParams.get('request');requests.push(id);return {ok:true,json:async()=>({calls:[{id,body}]})};},
 requestAnimationFrame(){},ResizeObserver:class{observe(){}},setTimeout,clearTimeout};
vm.createContext(context);
vm.runInContext(fs.readFileSync('agentpair/web_assets/analysis_view.js','utf8'),context);
const source=fs.readFileSync('agentpair/web_assets/model_security.js','utf8');
vm.runInContext(source.slice(source.indexOf('function refreshAnalysis('),source.indexOf('function render(){')),context);
context.time=value=>String(value);
let pending;
const actual=context.window.renderAnalysisView;
context.window.renderAnalysisView=(...args)=>(pending=actual(...args));

(async()=>{
 const first={id:'same-finding',kind:'server_password',label:'凭据',deviceId:'device',evidenceRevision:'rev1',evidence:[evidence('request-1'),evidence('request-2')]};
 context.refreshAnalysis(first);await pending;
 assert.deepEqual(requests,['request-1']);
 get('analysis-request').value='request-2';await get('analysis-request').onchange();
 assert.equal(requests.at(-1),'request-2');
 const updated={...first,evidenceRevision:'rev2',evidence:[evidence('request-3'),...first.evidence]};
 context.refreshAnalysis(updated);await pending;
 assert.equal(get('analysis-request').children.length,3,'new request is visible for the same finding');
 assert.equal(get('analysis-request').value,'request-2','selected request survives reordering');
 assert.deepEqual(requests,['request-1','request-2','request-2'],'refresh loads preserved request once, without loading another record first');
 context.refreshAnalysis(updated);await pending;assert.equal(requests.length,3,'unchanged evidence does not reload or reset replay');
 context.refreshAnalysis({...updated,evidenceRevision:'rev3',evidence:[evidence('request-3')]});await pending;
 assert.equal(get('analysis-request').value,'request-3','removed selected request falls back to actual available evidence');
 context.refreshAnalysis({...first,id:'other-finding',evidenceRevision:'other-rev',evidence:[evidence('other-request')]});await pending;
 assert.equal(requests.at(-1),'other-request','another finding cannot reuse prior request');
 assert(context.localAuditStatus({receivedRecords:0}).includes('尚无模型输入记录'));
 const status=context.localAuditStatus({receivedRecords:2,scannedRecords:2,lastReceivedAt:200,lastScannedAt:201});
 assert(status.includes('最近接收：200'));assert(status.includes('最近规则检查：201'));
 console.log('PASS same-finding evidence refresh, stable request selection, single verified body fetch, unchanged replay preservation, distinct receive/scan coverage');
})().catch(error=>{console.error(error);process.exitCode=1;});
