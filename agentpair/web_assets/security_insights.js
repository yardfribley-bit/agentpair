(() => {
  'use strict';
  const node = document.getElementById('session-security');
  if (!node) return;
  const text = (tag, value, cls) => { const n = document.createElement(tag); n.textContent = value; if (cls) n.className = cls; return n; };
  const stamp = value => {
    if (!value) return '尚无记录';
    const d = new Date(typeof value === 'number' ? value * 1000 : value);
    return Number.isNaN(d.valueOf()) ? '时间未记录' : d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
  };
  let last = '', generation = 0;
  async function load() {
    const seq = ++generation;
    try {
      const selected = document.getElementById('device')?.value;
      const response = await fetch('/api/insights/findings' + (selected ? '?device=' + encodeURIComponent(selected) : ''), { cache: 'no-store' });
      if (!response.ok) throw Error('分析状态暂时无法读取');
      const data = await response.json();
      if (seq !== generation) return;
      const signature = JSON.stringify(data);
      if (signature === last) return;
      last = signature;
      const summary = data.coverage || {};
      node.replaceChildren(text('h2', '会话记录中的安全发现'));
      node.append(text('p', 'SessionLens · 最近接收 ' + stamp(summary.latestReceived) + ' · ' + (summary.completed || 0) + ' 份当前分析 · ' + (summary.pending || 0) + ' 份待分析或有新记录', 'muted'));
      if (summary.running || summary.queued) node.append(text('p', (summary.running || 0) + ' 份分析中，' + (summary.queued || 0) + ' 份排队中。'));
      if (!data.items?.length) node.append(text('p', summary.pending ? '新记录已接收，尚未完成分析。当前不能据此判断有没有新的安全问题。' : '已完成的会话分析尚未列出安全发现；分析只覆盖选取的记录。'));
      for (const finding of data.items || []) {
        const detail = document.createElement('details');
        const header = text('summary', finding.title + (finding.stale ? ' · 有新记录待复核' : ''));
        detail.append(header, text('p', finding.fact));
        detail.append(text('p', '可能影响：' + finding.impact), text('p', '处理方法：' + finding.remediation));
        detail.append(text('p', finding.application + ' · 会话 ' + finding.sessionId + ' · 分析于 ' + stamp(finding.analyzedAt), 'muted'));
        detail.append(text('p', finding.evidenceBasis, 'muted'));
        const link = text('a', '查看任务与证据 →');
        link.href = '/session-insights/?insight=' + encodeURIComponent(finding.insightId) + '#' + encodeURIComponent(finding.evidenceRefs[0]);
        detail.append(link); node.append(detail);
      }
      const all = text('a', '查看最新会话与分析状态 →'); all.href = '/session-insights/'; node.append(all);
    } catch (error) {
      if (seq !== generation) return;
      node.replaceChildren(text('h2', '会话记录中的安全发现'), text('p', error.message, 'muted'));
    }
  }
  document.getElementById('device')?.addEventListener('change', load);
  document.getElementById('refresh')?.addEventListener('click', load);
  load(); setInterval(() => { if (!document.hidden) load(); }, 20000);
})();
