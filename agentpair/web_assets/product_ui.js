/* Shared Web navigation only. All state-changing actions remain in page controllers. */
(() => {
 'use strict';
 const paths={task:'M4 4h16v16H4z M8 8h8 M8 12h8 M8 16h5',devices:'M3 4h18v12H3z M8 21h8 M12 16v5',data:'M4 4h16v16H4z M8 4v16 M4 10h16 M4 15h16',security:'M12 3 21 7v5c0 5-9 9-9 9s-9-4-9-9V7z M8 12l3 3 5-6',insights:'M3 5h18v12H9l-5 4v-4H3z M7 9h10 M7 13h6',cloud:'M6 18h12a4 4 0 0 0 0-8 6 6 0 0 0-11-2 5 5 0 0 0-1 10z',packages:'M3 7l9-4 9 4v10l-9 4-9-4z M3 7l9 4 9-4 M12 11v10'};
 const nav=[['/','任务中心','Task center','task'],['/devices','我的设备','Devices','devices'],['/model-data','采集数据','Collected data','data'],['/model-security','交互安全审计','Security audit','security'],['/session-insights/','会话洞察','Session insights','insights'],['/cloud-machines','云机器','Cloud machines','cloud'],['/packages','软件包','Packages','packages']];
 const english=document.documentElement.lang.startsWith('en');
 const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
 const icon=kind=>{const span=node('span','ap-icon');span.setAttribute('aria-hidden','true');span.innerHTML='<svg viewBox="0 0 24 24"><path d="'+paths[kind]+'"/></svg>';return span;};
 function setup(){
  document.body.classList.add('agentpair-web');
  const aside=document.querySelector('body>aside');if(!aside)return;
  let navigation=aside.querySelector('nav');if(!navigation){navigation=node('nav');aside.querySelector('.brand')?.after(navigation);}
  navigation.className='primary-nav';navigation.setAttribute('aria-label',english?'Main navigation':'主导航');
  const existing=new Map([...navigation.querySelectorAll('a')].map(a=>[a.getAttribute('href'),a]));
  for(const [url,zh,en,kind] of nav){const link=existing.get(url)||node('a');link.href=url;link.replaceChildren(icon(kind),node('span',null,english?en:zh));const active=document.body.dataset.platformModule==='insights'?url==='/session-insights/':url==='/'?location.pathname==='/':location.pathname.startsWith(url);link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');if(url==='/cloud-machines')link.id='cloud-machines-link';if(url==='/packages')link.id='packages-link';navigation.append(link);}
  for(const child of [...navigation.children])if(!nav.some(n=>n[0]===child.getAttribute('href')))child.remove();
  if(!aside.querySelector('.ap-side-foot')){const foot=node('div','ap-side-foot',english?'AgentPair · Agent workspace':'AgentPair · Agent 工作台');aside.append(foot);}
  const productStyle=document.querySelector('link[data-product-ui]');if(productStyle)document.head.append(productStyle);
  document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;for(const panel of document.querySelectorAll('.resource-drawer[open]'))panel.open=false;const login=document.getElementById('login');if(login&&!login.classList.contains('hidden')){login.classList.add('hidden');document.getElementById('open-login')?.focus();}});
  document.addEventListener('agentpair:task-rendered',()=>{const status=document.getElementById('status'),t=window.agentpairCurrentTask;if(!status||!t)return;status.classList.remove('ap-status-success','ap-status-danger','ap-status-attention');status.classList.add(t.status==='completed'?'ap-status-success':['failed','interrupted'].includes(t.status)?'ap-status-danger':['blocked','awaiting_confirmation','needs_information'].includes(t.status)?'ap-status-attention':'ap-status-running');});
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})();
