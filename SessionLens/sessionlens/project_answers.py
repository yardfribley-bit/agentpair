"""Concise project answers generated from query-service results."""
import re
from html import escape as e
from .project_window import task_preview

def render_project_answer(result):
    p=result['projectDetails']
    text='<style>body{color:#233247;font-size:14px}p,li{line-height:1.6}h3{font-size:17px}a{color:#1769ef;text-decoration:none}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #e6e8ec;text-align:left}</style>'
    text+='<h3>'+e(p['name'])+'</h3><p><b>'+str(p['taskCount'])+' 个已关联开发任务</b> · '+str(p['fileCount'])+' 个源码文件 · '+str(p['writes'])+' 次写入或修改请求</p>'
    text+='<p>来源：'+e(p['source'])+' · 近期活动：'+e(p['lastUpdated'][:10] or '日期未记录')+'</p>'
    original=next((t for t in sorted(p['tasks'],key=lambda t:t['updated']) if re.search(r'开发|创建|新建|做一个|实现|修改|修复|添加|增加|写一个',t['prompt']) and not t['prompt'].lstrip().startswith(('继续','好','已发布','发布了','现在','这次'))),None)
    if original:text+='<p><b>关联的开发要求：</b>“'+e(task_preview(original['prompt'].replace('\n',' '))[:180])+'” <a href="project-task:'+original['taskId']+'">查看这次任务</a></p>'
    text+='<h3>里面有哪些内容</h3><p>记录中的文件类型：'+e('、'.join(k.upper()+' '+str(v)+' 个' for k,v in p['languages'].items()))+'</p><table><tr><th>内容目录</th><th>源码文件</th></tr>'
    text+=''.join('<tr><td>'+e(c['name'])+'</td><td>'+str(c['fileCount'])+'</td></tr>' for c in p['components'][:8])+'</table>'
    text+='<h3>关联任务里做过哪些动作</h3><p>'+e('、'.join(t['name']+' '+str(t['count'])+' 次' for t in p['tools'][:8]))+'</p>'
    text+='<p>已记录思路 '+str(p['recordKinds'].get('解题思路',0))+' 条 · 用户提问 '+str(p['recordKinds'].get('用户提问',0))+' 条 · 工具返回 '+str(p['recordKinds'].get('工具返回',0))+' 条。查看具体任务可追溯需求、调用参数和结果。</p>'
    text+='<h3>相关任务</h3>'
    tasks=p['tasks'][:6]
    for t in tasks:text+='<p><a href="project-task:'+t['taskId']+'">'+e(task_preview(t['prompt'].replace('\n',' '))[:120])+'</a><br><span style="color:#667085">'+e(t['updated'][:16].replace('T',' '))+'</span></p>'
    if p['taskCount']>6:text+='<p><a href="projects:">在项目总览查看其余任务 →</a></p>'
    text+='<p style="color:#667085">'+e(p['coverage'])+' 工具动作统计属于这些关联任务，可能包含跨项目操作。</p>'
    if not result.get('projectIndexComplete'):text+='<p>历史整理仍在进行，数量还会增加。</p>'
    return text
