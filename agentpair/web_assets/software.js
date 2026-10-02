(() => {
 const $=id=>document.getElementById(id);let csrf='',items=[],taskId='';
 async function api(path,data){const r=await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:data?JSON.stringify(data):undefined});const body=await r.json();if(!r.ok)throw Error(body.error||'请求失败');return body;}
 function options(el,rows,label){el.replaceChildren();for(const row of rows){const o=document.createElement('option');o.value=row.id;o.textContent=label(row);el.append(o);}}
 function recipes(){const device=items.find(x=>x.id===$('device').value);options($('software'),window.softwareRecipes.filter(x=>x.platform===device?.snapshot?.os),x=>x.name+' · '+(x.version||'系统软件源版本'));}
 async function load(){const session=await api('/api/session');if(session.role!=='admin')throw Error('请先登录管理员账号');csrf=session.csrf;items=(await api('/api/devices')).items;window.softwareRecipes=(await api('/api/software')).items;options($('device'),items.filter(x=>['Windows','Linux'].includes(x.snapshot?.os)),x=>x.name+' · '+(x.online?'在线':'离线'));recipes();}
 $('device').onchange=recipes;
 $('register').onsubmit=async e=>{e.preventDefault();try{await api('/api/software/register',{...Object.fromEntries(new FormData(e.target)),platform:'Windows'});await load();$('notice').textContent='方案已保存';}catch(err){$('notice').textContent=err.message;}};
 $('install').onsubmit=async e=>{e.preventDefault();if(!confirm('确认在所选设备安装此软件？安装可能修改系统，应用登录不自动完成。'))return;try{const r=await api('/api/software/install',{deviceId:$('device').value,softwareId:$('software').value});taskId=r.taskId;$('notice').textContent='安装任务已排队：'+taskId;await poll();}catch(err){$('notice').textContent=err.message;}};
 async function poll(){if(!taskId)return;try{const tasks=(await api('/api/devices/tasks')).items,t=tasks.find(x=>x.taskId===taskId);if(!t)return;const r=t.result||{};$('status').textContent=t.state+' · '+(r.summary||'等待执行端领取');$('log').textContent=(r.log||'')+(r.evidence?'\n'+JSON.stringify(r.evidence,null,2):'');if(['completed','failed','blocked'].includes(t.state))taskId='';}catch(err){$('notice').textContent=err.message;}}
 load().catch(e=>{$('notice').textContent=e.message;});setInterval(poll,3000);
})();
