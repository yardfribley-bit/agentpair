
(()=>{
if(new URLSearchParams(location.search).get('insights')!=='login')return;
const setup=()=>{
const login=document.getElementById('login');
if(login){login.classList.remove('hidden');login.scrollIntoView({block:'center'});}
const hint=document.createElement('p');hint.textContent='查看会话洞察需要登录。登录成功后将自动进入。';hint.className='notice';login?.prepend(hint);
let stopped=false;
const check=async()=>{if(stopped)return;try{const r=await fetch('/api/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok)return;const data=await r.json();if(data.csrf){stopped=true;location.replace('/session-insights/');}}catch(_){}};
check();const timer=setInterval(()=>{if(stopped)clearInterval(timer);else check();},1500);
};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})();
