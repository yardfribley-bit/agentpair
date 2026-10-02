"""OTLP/HTTP JSON metadata-only receiver; rejects content attributes."""
import re
ALLOWED={'gen_ai.operation.name','gen_ai.request.model','gen_ai.usage.input_tokens',
         'gen_ai.usage.output_tokens','applens.source','applens.duration.observed'}
def metadata_spans(payload):
    rows=[]
    for resource in payload.get('resourceSpans',[]):
        for scope in resource.get('scopeSpans',[]):
            for span in scope.get('spans',[]):
                if len(rows)>=2000:raise ValueError('Too many spans')
                if not re.fullmatch('[0-9a-f]{32}',span.get('traceId','')) or not re.fullmatch('[0-9a-f]{16}',span.get('spanId','')):raise ValueError('Invalid span identity')
                attrs={}
                for item in span.get('attributes',[]):
                    key=item.get('key')
                    if key not in ALLOWED:raise ValueError('Content or unsupported attribute rejected')
                    value=item.get('value',{})
                    if not isinstance(value,dict) or len(value)!=1 or not set(value)<= {'stringValue','intValue','boolValue'}:raise ValueError('Invalid typed attribute')
                    if len(str(value))>400:raise ValueError('Attribute too large')
                    attrs[key]=value
                stamp=span.get('endTimeUnixNano')
                if not isinstance(stamp,str) or not stamp.isdigit() or len(stamp)>20:raise ValueError('Invalid completion time')
                rows.append({'traceId':span['traceId'],'spanId':span['spanId'],'endTimeUnixNano':stamp,'attributes':attrs})
    return rows
