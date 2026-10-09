import json,subprocess
from pathlib import Path
base=Path(__file__).resolve().parent;(base/'build').mkdir(exist_ok=True)
scenes=json.loads((base/'storyboard.json').read_text())
for s in scenes:
 p=base/'build'/f"{s['id']}.aiff"
 subprocess.run(['/usr/bin/say','-v','Tingting','-r','220','-o',str(p),s['voice']],check=True)
 r=subprocess.run(['/usr/local/bin/ffprobe','-v','error','-show_entries','format=duration','-of','json',str(p)],capture_output=True,text=True,check=True)
 s['audio']=str(p);s['duration']=max(s['duration'],round(float(json.loads(r.stdout)['format']['duration'])+.7,2))
 print(s['id'],s['duration'])
(base/'build'/'scenes.json').write_text(json.dumps(scenes,ensure_ascii=False,indent=2))
print('total',sum(s['duration'] for s in scenes))
