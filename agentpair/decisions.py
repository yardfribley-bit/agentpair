"""Typed atomic checks; no claimed Jev API or calibrated confidence."""
import datetime
import math

QUESTIONS={
    'goal_met':'是否回答了用户要求',
    'grounded':'关键结论是否有可核查依据',
    'consistent':'答复是否与证据或交付物一致',
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
    values=[c['value'] for c in checks]
    action='deliver' if all(v=='yes' for v in values) else 'recheck'
    if 'unknown' in values: action='needs_information'
    if answer.get('citationWarning'): action='recheck'
    return {'action':action,'checks':checks,'confidence':None,
            'confidenceNote':'未校准，不将模型自述当作准确率',
            'policyVersion':1}
