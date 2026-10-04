const slots = ['dashboard', 'list', 'detail'];
const labels = {dashboard:'总览', list:'会话列表', detail:'会话详情'};
const status = document.getElementById('status');
const key = 'backplanes_three_pages_v1';
async function readPages() { const data = await chrome.storage.local.get(key); return data[key] || {}; }
async function refresh() { const pages = await readPages(); slots.forEach(s => document.getElementById(s).textContent = pages[s] ? `已保存 · ${pages[s].text.length.toLocaleString()} 字符` : '未保存'); }
function escapeHtml(s) { return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
for (const button of document.querySelectorAll('[data-slot]')) button.addEventListener('click', async () => {
  try {
    const slot = button.dataset.slot;
    const [tab] = await chrome.tabs.query({active:true, currentWindow:true});
    if (!tab?.url) throw new Error('请打开 Backplanes 页面。');
    const url = new URL(tab.url);
    if (url.origin !== 'https://app.backplanes.com') throw new Error('仅支持 app.backplanes.com。');
    const valid = slot === 'dashboard' ? url.pathname === '/dashboard' : slot === 'list' ? /^\/reports\/me\/?$/.test(url.pathname) : /^\/reports\/me\/[^/]+\/?$/.test(url.pathname);
    if (!valid) throw new Error(`当前不是${labels[slot]}页，请进入对应页面后保存。`);
    status.textContent = '正在保存页面…';
    const [{result}] = await chrome.scripting.executeScript({target:{tabId:tab.id}, func:() => {
      const styles = new Set();
      const props = ['display','position','box-sizing','width','max-width','min-width','height','margin','padding','gap','grid-template-columns','grid-column','flex-direction','flex-wrap','align-items','justify-content','font-family','font-size','font-weight','line-height','letter-spacing','text-align','color','background-color','border','border-radius','box-shadow','white-space','overflow-wrap','list-style'];
      const clone = document.body.cloneNode(true);
      const originals = [document.body, ...document.body.querySelectorAll('*')];
      const copies = [clone, ...clone.querySelectorAll('*')];
      originals.forEach((el,i) => {
        const cs = getComputedStyle(el); const copy = copies[i];
        copy.setAttribute('style', props.map(p => `${p}:${cs.getPropertyValue(p)}`).join(';') + (cs.position === 'fixed' ? ';position:relative;top:auto;left:auto;right:auto;bottom:auto' : ''));
        const family = cs.fontFamily; styles.add(family);
      });
      clone.querySelectorAll('script,iframe,object,embed,link,style,input,textarea').forEach(el => el.remove());
      [clone, ...clone.querySelectorAll('*')].forEach(el => {
        for(const attr of [...el.attributes]) if (/^on/i.test(attr.name) || ['src','srcset','href','action','formaction','nonce','integrity'].includes(attr.name)) el.removeAttribute(attr.name);
      });
      return {title:document.title, url:location.href, text:document.body.innerText, html:clone.outerHTML, capturedAt:new Date().toISOString(), viewport:{width:innerWidth,height:innerHeight}};
    }});
    if (!result?.text?.trim()) throw new Error('页面没有正文，请等待加载。');
    const pages = await readPages(); pages[slot] = result;
    await chrome.storage.local.set({[key]:pages}); await refresh();
    status.textContent = `已保存${labels[slot]}，可以继续进入下一页。`;
  } catch(error) { status.textContent = error.message; }
});
document.getElementById('download').addEventListener('click', async () => {
  try {
    const pages = await readPages(); let count = 0;
    for(const slot of slots) {
      const p = pages[slot]; if(!p) continue;
      const base = `backplanes-${slot}-${p.capturedAt.replace(/[:.]/g,'-')}`;
      const text = `# ${p.title}\n\n来源：${p.url}\n导出时间：${p.capturedAt}\n\n${p.text}\n`;
      const html = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:"><title>${escapeHtml(p.title)}</title></head>${p.html}</html>`;
      for(const [extension,content,mime] of [['txt',text,'text/plain'],['html',html,'text/html']]) {
        const blob = URL.createObjectURL(new Blob([content],{type:`${mime};charset=utf-8`}));
        try { await chrome.downloads.download({url:blob,filename:`${base}.${extension}`,saveAs:false}); count++; }
        finally { setTimeout(()=>URL.revokeObjectURL(blob),60000); }
      }
    }
    status.textContent = count ? `已发起 ${count} 个文件下载。HTML 保留布局；图像、特殊字体及动画可能与原页不同。` : '请先保存页面。';
  } catch(error) { status.textContent = error.message; }
});
document.getElementById('clear').addEventListener('click', async () => { await chrome.storage.local.remove(key); await refresh(); status.textContent='暂存已清除。'; });
refresh().catch(error => { status.textContent = error.message; });
