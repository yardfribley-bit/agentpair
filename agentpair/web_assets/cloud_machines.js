(() => {
 const $=id=>document.getElementById(id);let csrf='',quote=null,machines=[],recipes=[],operationId=sessionStorage.getItem('cloud-install-operation')||'',installBusy=!!operationId;
 async function api(path,data){const r=await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:data?JSON.stringify(data):undefined});let b;try{b=await r.json();}catch(_){throw Error("服务暂时无法返回数据（HTTP "+r.status+"）。请保留本次输入并核对状态，再重试。");}if(!r.ok)throw Error(b.error||'请求失败');return b;}
 function notice(text){$('notice').textContent=text;}
 const active=m=>m.state==='active'&&new Date(m.expiresAt)>new Date();
 function updateInstall(){ $('install').disabled=installBusy||!$('install-machine').value||!$('install-software').value; }
 function option(select,rows,label){select.replaceChildren();for(const row of rows){const item=document.createElement('option');item.value=row.id;item.textContent=label(row);select.append(item);}}
 async function refresh(){const selected=$('install-machine').value;machines=(await api('/api/cloud/machines')).items;const list=$('machines');list.replaceChildren();if(!machines.length)list.textContent='当前没有由本页面创建的机器。';for(const machine of machines){const card=document.createElement('div');card.className='machine';const title=document.createElement('strong');title.textContent=machine.name+' · '+machine.platform;const detail=document.createElement('small');detail.textContent='记录状态：'+({active:'租约有效',released:'已回收',reconcile_required:'待核对资源'}[machine.state]||machine.state)+' · 租约到期：'+new Date(machine.expiresAt).toLocaleString('zh-CN');const check=document.createElement('button');check.textContent='检查登录';const connection=document.createElement('button');connection.textContent='查看连接信息';const status=document.createElement('div');status.className='muted';check.onclick=async()=>{check.disabled=true;try{const r=await api('/api/cloud/machines/'+machine.id+'/login');status.textContent='云端：'+r.cloudState+' · 公网：'+(r.address||'待分配')+' · SSH：'+r.loginState+(r.loginUser?' · 用户：'+r.loginUser:'');}catch(e){status.textContent=e.message;}finally{check.disabled=false;}};connection.onclick=async()=>{try{const r=await api('/api/cloud/machines/'+machine.id+'/connection');status.textContent='地址：'+(r.address||'待分配')+' · 用户：'+r.username+(r.password?' · 密码：'+r.password:' · SSH 密钥登录');}catch(e){status.textContent=e.message;}};const start=document.createElement('button');start.textContent='启动机器';start.onclick=async()=>{start.disabled=true;try{const r=await api('/api/cloud/start',{leaseId:machine.id});status.textContent=r.startState==='already_running'?'机器已运行；请检查登录':'已请求启动；请稍后检查登录';}catch(e){status.textContent=e.message;}finally{start.disabled=false;}};if(!active(machine)){start.disabled=true;check.disabled=true;connection.disabled=true;status.textContent='租约已结束，无法启动、连接或安装';}card.append(title,detail,document.createElement('br'),start,check,connection,status);list.append(card);}option($('install-machine'),machines.filter(active),x=>x.name+' · '+x.platform);if(machines.some(x=>active(x)&&x.id===selected))$('install-machine').value=selected;filterSoftware();}
 function filterSoftware(){const machine=machines.find(x=>x.id===$('install-machine').value);option($('install-software'),recipes.filter(x=>x.platform.toLowerCase()===machine?.platform),x=>x.name+' · '+(x.version||'软件源版本'));$('install-target').textContent=machine?'安装目标：'+machine.name+' · '+machine.platform+' · '+machine.id:'没有租约有效的机器';updateInstall();}
 $('install-machine').onchange=filterSoftware;
 $('install-software').onchange=updateInstall;
 function invalidate(){quote=null;$('price').textContent='配置已改变，请重新报价';$('create').disabled=true;}
 function sizing(){return {cpu:Number($('cpu').value),memoryGB:Number($('memory').value),systemDiskGB:Number($('system-disk').value),dataDiskGB:Number($('data-disk').value)};}
 $('system').onchange=()=>{const win=$('system').value==='Windows';$('cpu').value=win?'2':'1';$('memory').value=win?'4':'1';$('system-disk').min=win?'40':'20';$('system-disk').value=win?'40':'20';$('data-disk').value=win?'20':'0';invalidate();};
 for(const id of ['cpu','memory','system-disk','data-disk'])$(id).oninput=invalidate;
 $('quote').onclick=async()=>{try{quote=await api('/api/cloud/quote',{system:$('system').value,sizing:sizing()});$('price').textContent='¥'+quote.hourlyCNY.toFixed(2)+'/小时（主机 ¥'+quote.hostCNY.toFixed(2)+' + 公网 ¥'+quote.eipCNY.toFixed(2)+'）；'+quote.cpu+' 核 '+Math.round(quote.memoryMB/1024)+' GB；'+quote.zone;$('create').disabled=false;notice('报价已更新');}catch(e){quote=null;$('create').disabled=true;notice(e.message);}};
 $('create').onclick=async()=>{if(!quote)return;const accepted=confirm('确认创建 '+quote.system+' 云机器？当前报价 ¥'+quote.hourlyCNY.toFixed(2)+'/小时，租约 60 分钟，可能立即产生费用。');if(!accepted)return;$('create').disabled=true;try{const r=await api('/api/cloud/create',{system:quote.system,sizing:quote.sizing,maxHourlyCNY:quote.hourlyCNY,requestId:crypto.randomUUID()});notice('创建请求已返回：'+r.name+'；机器启动和 SSH 登录还需核验。');await refresh();}catch(e){notice(e.message);}finally{quote=null;}};
 $('refresh').onclick=()=>refresh().catch(e=>notice(e.message));
 $('install').onclick=async()=>{
  if(installBusy)return;
  const leaseId=$('install-machine').value,softwareId=$('install-software').value;
  if(!leaseId||!softwareId){notice('请选择机器和软件');return;}
  installBusy=true;updateInstall();
  try{const r=await api('/api/cloud/install',{leaseId,softwareId});operationId=r.id;sessionStorage.setItem('cloud-install-operation',operationId);$('install-status').textContent='已提交安装，正在连接目标机器…';await poll();}
  catch(e){$('install-status').textContent=e.message;installBusy=false;updateInstall();}
 };
 let polling=false;
 async function poll(){
  if(!operationId||polling)return;polling=true;
  try{
   const op=await api('/api/cloud/operations/'+operationId);
   const labels={queued:'等待执行',running:'执行中',completed:'安装与版本验证通过',failed:'执行失败',interrupted:'结果待核验',verification_required:'安装已完成，版本待核验'};
   const stages={waiting:'等待执行',transfer:'传送方案',download:'下载安装包',install:'执行安装',verify:'核验版本',verified:'版本已核验',receipt_missing:'回执缺失',failed:'执行未完成'};
   $('operation-target').textContent='本次任务目标：'+(op.machineName||op.leaseId)+' · '+(op.softwareName||op.softwareId)+(op.expectedVersion?' · '+op.expectedVersion:'');
   $('install-status').textContent=(labels[op.state]||op.state)+' · '+(stages[op.stage]||op.stage||'')+'：'+op.summary;
   $('function-status').textContent='应用启动与翻译功能：尚未验证。安装及版本核验通过后，仍需在目标应用中实际测试。';
   const evidence=$('install-evidence');evidence.hidden=!op.evidence;evidence.textContent=op.evidence?JSON.stringify(op.evidence,null,2):'';
   if(['completed','failed','interrupted','verification_required'].includes(op.state)){
    operationId='';sessionStorage.removeItem('cloud-install-operation');
    installBusy=['interrupted','verification_required'].includes(op.state);updateInstall();
   }
  }catch(e){notice('暂时无法读取安装进度，任务结果尚未确认，将继续查询：'+e.message);}
  finally{polling=false;}
 }
 async function init(){const s=await api('/api/session');if(s.role!=='admin')throw Error('此页面仅管理员可用');csrf=s.csrf;recipes=(await api('/api/software')).items;await refresh();await poll();}
 init().catch(e=>{notice(e.message);$('machines').textContent='读取失败，请修复后点击刷新机器';});setInterval(poll,3000);
})();
