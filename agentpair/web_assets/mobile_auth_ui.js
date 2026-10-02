(() => {
 const $=id=>document.getElementById(id);let csrf='',device=null,request=null,taskList=[],timer=null;
 const status=(text)=>{$('mobile-state').textContent=text;};
 async function api(path,data){const response=await fetch(path,{method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:data?JSON.stringify(data):undefined});const body=await response.json();if(!response.ok)throw Error(body.error||'请求失败');return body;}
 async function load(){try{const session=await api('/api/session');csrf=session.csrf||'';if(!csrf)return;const devices=await api('/api/devices');device=devices.items.find(x=>x.id===window.agentpairSelectedDevice?.id)||null;const card=$('mobile-login');card.hidden=!device||device.snapshot?.os!=='Android';if(card.hidden)return;
   const tasks=await api('/api/tasks');taskList=tasks.items;const select=$('mobile-task'),previous=select.value;select.replaceChildren();for(const task of taskList){const option=document.createElement('option');option.value=task.id;option.textContent=task.title+' · '+task.status;select.append(option);}select.value=taskList.some(x=>x.id===previous)?previous:(device.currentTaskId||taskList[0]?.id||'');$('mobile-start').disabled=!select.value||!!request;
   if(request){const current=await api('/api/mobile-auth/'+encodeURIComponent(request.id));if(current.state==='received'){status('手机已将匹配验证码安全回传。浏览器登录尚未确认完成。');}else status('等待 '+current.origin+' · 服务标记 '+current.brand+' 的验证码；请求三分钟后失效。');}
  }catch(error){status(error.message);}}
 $('mobile-login-form').addEventListener('submit',async e=>{e.preventDefault();if(request||!device)return;try{request=await api('/api/mobile-auth',{deviceId:device.id,taskId:$('mobile-task').value,origin:$('mobile-origin').value.trim(),brand:$('mobile-brand').value.trim()});$('mobile-cancel').hidden=false;$('mobile-start').disabled=true;status('登录请求已创建，等待手机短信。');await load();timer=setInterval(load,2500);}catch(error){request=null;status(error.message);}});
 $('mobile-cancel').addEventListener('click',async()=>{if(!request)return;clearInterval(timer);try{await api('/api/mobile-auth/cancel',{id:request.id});}catch(_){}request=null;$('mobile-cancel').hidden=true;$('mobile-start').disabled=false;status('登录请求已取消。');});
 window.addEventListener('agentpair:device-selected',event=>{device=event.detail;request=null;clearInterval(timer);$('mobile-cancel').hidden=true;if($('mobile-login'))load();});
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',load);else load();
})();
