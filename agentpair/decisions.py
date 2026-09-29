"""Typed atomic checks; no claimed Jev API or calibrated confidence."""
import datetime
import math
import json
import re

QUESTIONS={
    'goal_met':'是否回答了用户要求',
    'grounded':'关键结论是否有可核查依据',
    'consistent':'答复是否与证据或交付物一致',
    'delivery':'最终答案是否直接回应用户并包含实际结果',
}

def decide(answer, evidence=None, tool=None):
    checks=[]
    supplied=answer.get('checks',{})
    if not isinstance(supplied,dict): supplied={}
    for key,label in QUESTIONS.items():
        item=supplied.get(key,{})
        if not isinstance(item,dict): item={}
        value=item.get('value')
        if value not in ('yes','no','unknown'): value='unknown'
        reason=item.get('reason')
        if not isinstance(reason,str) or not reason.strip():
            value='unknown'; reason='缺少判断依据'
        checks.append({'id':key,'question':label,'value':value,'reason':reason,'source':'model_judgment'})
    final=answer.get('finalAnswer','')
    readable=isinstance(final,str) and len(final.strip())>=8
    if readable:
        try:
            json.loads(final.strip().removeprefix('```json').removesuffix('```'))
            readable=False
        except (ValueError,TypeError): pass
        if re.fullmatch(r'[\s。！.!]*(?:查询成功|任务完成|已完成|已查询成功|结果见JSON|Driver已完成)[\s。！.!]*',final): readable=False
    checks.append({'id':'readable_answer','question':'是否提供可直接阅读的最终答案','value':'yes' if readable else 'no','reason':'必须有独立的自然语言结果，不能只返回原始 JSON 或完成通知','source':'deterministic'})
    if tool=='weather':
        e=evidence if isinstance(evidence,dict) else {}
        def rule(key,label,ok,reason):
            checks.append({'id':key,'question':label,'value':'yes' if ok else 'no','reason':reason,'source':'deterministic'})
        current=e.get('current',{}); units=e.get('units',{})
        temp=current.get('temperature_2m')
        rule('data','是否取得有效天气数值',isinstance(temp,(int,float)) and not isinstance(temp,bool) and math.isfinite(temp) and units.get('temperature_2m')=='°C','检查工具返回值及摄氏度单位')
        try:
            stamp=datetime.datetime.fromisoformat(current['time']).replace(tzinfo=datetime.timezone.utc)
            fresh=abs((datetime.datetime.now(datetime.timezone.utc)-stamp).total_seconds())<7200 and e.get('timezone')=='UTC'
        except (KeyError,TypeError,ValueError): fresh=False
        rule('fresh','数据是否在两小时内',fresh,'根据原始时间重新计算，不采用模型自报的新鲜度')
        rule('source','是否保留来源',bool(e.get('sourceUrl')),'检查工具来源链接')
        final=final if isinstance(final,str) else ''
        temperatures=re.findall(r'(-?\d+(?:\.\d+)?)\s*(?:°\s*C|℃|摄氏度)',final,re.I)
        matches=isinstance(temp,(int,float)) and any(abs(float(x)-temp)<=0.05 for x in temperatures)
        rule('delivered_temperature','最终答案是否包含证据中的温度',matches,'核对最终答案中的摄氏温度，不能仅在证据里提供')
        rule('delivered_context','最终答案是否说明时间和来源',bool(re.search(r'\d{1,2}:\d{2}',final)) and str(e.get('source','Open-Meteo')).lower() in final.lower(),'时间与来源必须直接出现在最终答案中')
    if tool=='github_repository':
        e=evidence if isinstance(evidence,dict) else {}
        checks.append({'id':'source_snapshot','question':'是否真正取得固定版本源码',
                       'value':'yes' if e.get('commit') and e.get('files') and not e.get('error') else 'unknown',
                       'reason':'需要 GitHub 提交和实际读取文件，不能用模型概述替代源码', 'source':'deterministic'})
    if 'executionValidated' in answer:
        checks.append({'id':'isolated_execution','question':'云端构建与测试是否真实通过',
                       'value':'yes' if answer['executionValidated'] else 'no',
                       'reason':'由执行器退出码判定，不能以模型自报覆盖', 'source':'deterministic'})
    if 'parallelValidated' in answer:
        checks.append({'id':'parallel_comparison','question':'两个方案是否返回并完成比较与选择','value':'yes' if answer['parallelValidated'] else 'no','reason':'需要两个成功分支、比较结果和明确选择','source':'deterministic'})
    values=[c['value'] for c in checks]
    action='deliver' if all(v=='yes' for v in values) else 'recheck'
    if 'unknown' in values: action='needs_information'
    if answer.get('citationWarning'): action='recheck'
    return {'action':action,'checks':checks,'confidence':None,
            'confidenceNote':'未校准，不将模型自述当作准确率',
            'policyVersion':2}
