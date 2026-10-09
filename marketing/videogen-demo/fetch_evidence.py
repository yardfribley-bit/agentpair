import json,urllib.parse,urllib.request
from pathlib import Path
base='https://www.chuhaijian.com'
out=Path(__file__).resolve().parent
url=base+'/api/data-center/search?'+urllib.parse.urlencode({'q':'tool="VideoGen"','pageSize':10})
with urllib.request.urlopen(url,timeout=45) as r:value=json.load(r)
(out/'search-evidence.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
(out/'search-evidence.json').chmod(0o600)
items=value.get('items',[])
print(json.dumps({'total':value.get('total'),'items':[{'id':i['id'],'agent':i.get('application'),'device':i.get('deviceName'),'tool':i.get('tool'),'input':(i.get('request') or {}).get('text'),'title':i.get('title'),'summary':i.get('summary')} for i in items[:4]]},ensure_ascii=False))
for index,item in enumerate(items[:3]):
 with urllib.request.urlopen(base+'/api/data-center/record?'+urllib.parse.urlencode({'id':item['id']}),timeout=45) as r:detail=json.load(r)
 p=out/f'record-evidence-{index}.json';p.write_text(json.dumps(detail,ensure_ascii=False),encoding='utf-8');p.chmod(0o600)
 d=detail['item'];print(json.dumps({'index':index,'arguments':d.get('arguments'),'result':d.get('result'),'input':d.get('taskContext'),'tool':d.get('tool')},ensure_ascii=False)[:4500])
