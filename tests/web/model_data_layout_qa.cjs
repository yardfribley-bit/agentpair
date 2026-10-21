/** Legacy viewer grid/pagination regression. All browser requests are synthetic. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assets=path.resolve('agentpair/web_assets');
const base='http://legacy-fixture.invalid';
const mime={'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml'};
(async()=>{
 const options={headless:true};if(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)options.executablePath=process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
 const browser=await chromium.launch(options),results=[];
 try{
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:960}}),page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.name));page.on('console',message=>{if(message.type()==='error')errors.push('console-error');});
   const call={id:'fixture-request',collector:'applens',collectorName:'AppLens',application:'WorkBuddy',recordType:'model_input',source:'workbuddy_network_context',timestamp:1700000000,sessionId:'synthetic-session',sessionName:'synthetic-session',modelEvidence:'模型输入',body:'{"messages":[{"role":"user","content":"synthetic only"}]}',bodyBytes:65,bodySHA256:'a'.repeat(64),recordStatus:'parseable'};
   await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());assert.equal(request.method(),'GET');
    let value;
    if(url.pathname==='/api/session')value={role:'viewer',username:null,csrf:null};
    else if(url.pathname==='/api/audit/devices')value={items:[{id:'fixture-device',name:'Synthetic device',online:false,lastActivity:1700000000}]};
    else if(url.pathname==='/api/audit/model-data/fixture-device')value=url.searchParams.has('request')?{device:{id:'fixture-device',name:'Synthetic device'},calls:[call],items:[]}:{device:{id:'fixture-device',name:'Synthetic device'},calls:[{...call,body:undefined}],items:[],total:413578,hasMore:true};
    if(value!==undefined)return route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
    const relative=url.pathname==='/model-data/raw'?'model_data.html':url.pathname.slice(1),file=path.join(assets,relative);
    if(file.startsWith(assets+path.sep)&&fs.existsSync(file)&&fs.statSync(file).isFile())return route.fulfill({contentType:mime[path.extname(file)]||'text/plain',body:fs.readFileSync(file)});
    return route.fulfill({status:204,body:''});
   });
   await page.goto(base+'/model-data/raw?scope=global&collector=all',{waitUntil:'networkidle'});
   await page.locator('#raw-pagination a').waitFor({state:'visible'});
   const result=await page.evaluate(()=>{
    const label=document.getElementById('request').closest('label'),pagination=document.getElementById('raw-pagination'),link=pagination.querySelector('a');
    return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,labelWidth:Math.round(label.getBoundingClientRect().width),linkWidth:Math.round(link.getBoundingClientRect().width),paginationWhiteSpace:getComputedStyle(pagination).whiteSpace,linkWhiteSpace:getComputedStyle(link).whiteSpace,totalDisplayed:/共 413578 条/.test(pagination.textContent)};
   });
   assert.equal(result.scrollWidth,result.width);assert.equal(result.paginationWhiteSpace,'normal');assert.equal(result.linkWhiteSpace,'normal');assert(result.linkWidth<=result.labelWidth);assert(result.totalDisplayed);assert.equal(errors.length,0);
   results.push({...result,errors:errors.length});await context.close();
  }
 }finally{await browser.close();}
 console.log(JSON.stringify({synthetic:true,networkRequestsSent:0,sourceContentExported:false,results}));
})().catch(error=>{console.error(error.name+': '+error.message);process.exitCode=1});
