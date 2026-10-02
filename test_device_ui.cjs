const {chromium}=require('/Users/jatsmith/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const path=require('path'),assert=require('assert');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
 const page=await browser.newPage({viewport:{width:1536,height:1150}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 let posted=null,platform='Windows',mockTasks=[];const application={name:'WorkBuddy',version:'1.0',publisher:'示例发布者',processNames:['workbuddy.exe']};
 await page.route('http://devices.test/**',async r=>{const p=new URL(r.request().url()).pathname;let data;
 if(p==='/api/session')data={csrf:'test',role:'admin'};
 else if(p==='/api/tasks')data={items:[]};
 else if(p==='/api/devices')data={items:[{id:platform==='Android'?'android-test':'test',name:platform==='Android'?'Android 测试设备':'Windows-031（测试数据）',online:true,lastSeen:Date.now()/1000,snapshot:{os:platform,architecture:platform==='Android'?'arm64-v8a':'AMD64',applications:platform==='Android'?[]:[application],processes:platform==='Android'?[]:[{name:'workbuddy.exe',pid:420,parentPid:100}],errors:[]}}]};
 else if(p==='/api/devices/tasks')data={items:mockTasks};
 else if(p==='/api/devices/dispatch'){posted=r.request().postDataJSON();mockTasks.unshift({taskId:'android-job',deviceId:'android-test',state:'queued',payload:posted.task,result:null});data={taskId:'android-job',state:'queued',deviceId:'android-test'};}
 else if(p==='/api/devices/pairing')data={code:'fixture-pair-code'};
 else if(p==='/api/devices/plan'){posted=r.request().postDataJSON();data={id:'plan-test',name:'WorkBuddy',scopes:['processes','applications'],steps:['读取应用','分析进程'],acceptance:['引用快照'],limitations:['没有网络连接']};}
 else if(p==='/api/devices/confirm'){data={taskId:'task-test',title:'应用分析 · WorkBuddy'};}
 else if(p==='/api/tasks/task-test')data={id:'task-test',title:'应用分析 · WorkBuddy',status:'running'};
 if(data)return r.fulfill({json:data});
 const file=path.join(__dirname,'agentpair/web_assets',p==='/devices'?'devices.html':p.slice(1));return r.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
 await page.goto('http://devices.test/devices');await page.locator('#application-picker option[value="applications:0"]').waitFor({state:'attached'});await page.locator('#application-picker').selectOption('applications:0');
 assert.equal(await page.locator('#selection').inputValue(),'WorkBuddy');
 await page.locator('#goal').fill('分析关联进程以及下一步证据缺口');await page.locator('#make-plan').click();await page.locator('#plan').waitFor();await page.locator('#share').check();await page.locator('#analyze').click();await page.locator('#result a').waitFor();assert.deepEqual(posted.selected,application);
 await page.locator('#inventory-panel summary').click();await page.locator('#kind').selectOption('processes');assert((await page.locator('#items').innerText()).includes('PID 420'));await page.locator('#inventory-panel summary').click();
 await page.screenshot({path:'/private/tmp/agentpair-devices.png',fullPage:true});
 await page.locator('#pair').click();await page.waitForFunction(()=>document.getElementById('pair-command').textContent.includes('fixture-pair-code'));assert((await page.locator('#pair-command').innerText()).includes('fixture-pair-code'));
 platform='Android';await page.locator('#refresh').click();await page.locator('#android-tasks').waitFor({state:'visible'});await page.locator('#android-task-goal').fill('确认任务送达并回传文字回执');await page.locator('#android-task-form button').click();await page.getByText('等待手机领取',{exact:false}).waitFor();assert.equal(posted.task.goal,'确认任务送达并回传文字回执');mockTasks[0].state='completed';mockTasks[0].result={summary:'手机端已收到并完成回执'};await page.locator('#refresh').click();await page.getByText('手机回执：手机端已收到并完成回执').waitFor();
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
 assert.equal(await page.locator('.downloads article').nth(2).isVisible(),true);console.log('PASS: inventory selection, evidence confirmation, Android cloud task dispatch/status, pairing, mobile download layout; no JS errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
