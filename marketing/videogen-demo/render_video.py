#!/usr/bin/env python3
"""Original product motion graphics, using synthetic defaults or explicitly selected local evidence."""
import argparse,json,math,re,subprocess,wave
from pathlib import Path
from functools import lru_cache
import numpy as np
from PIL import Image,ImageDraw,ImageFont
BASE=Path(__file__).resolve().parent;BUILD=BASE/'build'
W,H,FPS=1920,1080,30
BG='#F7F8FA';INK='#142238';MUTED='#617084';BLUE='#2563EB';LINE='#E1E6EE';GREEN='#18865A';AMBER='#B36C0B'
FONT='/System/Library/Fonts/STHeiti Light.ttc';BOLD='/System/Library/Fonts/STHeiti Medium.ttc';MONO='/System/Library/Fonts/Menlo.ttc';EN='/System/Library/Fonts/Supplemental/Arial Bold.ttf'
parser=argparse.ArgumentParser()
parser.add_argument('--preview',action='store_true')
parser.add_argument('--evidence-dir',type=Path,help='Explicitly opt in to locally retained, authorized evidence')
ARGS=parser.parse_args()
DEMO_MODE=ARGS.evidence_dir is None
DEMO_DATA={
 'deviceName':'演示设备','application':'示例 Agent',
 'request':{'text':'生成协议流程动画，并核对输出文件的规格。'},
 'arguments':{'params':{'prompt':'创建协议流程动画，使用清晰标签；完成后核对文件规格。','aspect_ratio':'16:9','resolution':'1080P','enable_audio':False}},
 'presentation':{'requestedSeconds':12,'observedDuration':8.2,'width':1920,'height':1080}}
DEMO_SEARCH={'total':3,'items':[{'title':v,'application':'示例 Agent'} for v in ['协议流程动画示例','工具调用示例','文件核验示例']]}
if DEMO_MODE:
 DATA,SEARCH=DEMO_DATA,DEMO_SEARCH
else:
 DATA=json.loads((ARGS.evidence_dir/'record-evidence-0.json').read_text())['item']
 SEARCH=json.loads((ARGS.evidence_dir/'search-evidence.json').read_text())
SCENES=json.loads((BUILD/'scenes.json').read_text())
PRESENTATION=DATA.get('presentation') or {}
REQUESTED=PRESENTATION.get('requestedSeconds')
OBSERVED=PRESENTATION.get('observedDuration')
def metric(value):return '待核对' if value is None else f'{value:g}' if isinstance(value,(int,float)) else str(value)
def evidence_shot(name):
 if not DEMO_MODE and (ARGS.evidence_dir/name).is_file():
  return Image.open(ARGS.evidence_dir/name).convert('RGB')
 im=Image.new('RGB',(1280,760),BG);d=ImageDraw.Draw(im)
 f=ImageFont.truetype(FONT,30)
 d.text((275,75),'AgentPair · 示例界面',font=f,fill=INK)
 for y,label in [(155,'工具调用示例'),(250,'用户需求与来源信息'),(345,'调用参数与结果核验')]:
  d.rounded_rectangle((265,y,1215,y+75),radius=12,fill='white',outline=LINE)
  d.text((290,y+20),label,font=f,fill=MUTED)
 return im
SHOT1=evidence_shot('video-search.jpg')
SHOT2=evidence_shot('video-call-result.jpg')
@lru_cache(maxsize=80)
def font(n=32,bold=False,mono=False,en=False):return ImageFont.truetype(MONO if mono else EN if en else BOLD if bold else FONT,n)
def text(im,xy,value,size=32,color=INK,bold=False,mono=False,en=False):ImageDraw.Draw(im).text(xy,str(value),font=font(size,bold,mono,en),fill=color,stroke_width=0)
def box(im,coords,fill='white',outline=LINE,r=22,width=2):ImageDraw.Draw(im).rounded_rectangle(coords,radius=r,fill=fill,outline=outline,width=width)
def wrap(value,maxwidth,size=32,bold=False,mono=False):
 lines=[];current='';f=font(size,bold,mono)
 for ch in value:
  if ch=='\n':lines.append(current);current='';continue
  if f.getlength(current+ch)>maxwidth and current:lines.append(current);current=ch
  else:current+=ch
 if current:lines.append(current)
 return lines
def lines(im,xy,value,width,size=32,color=INK,bold=False,mono=False,spacing=1.65,maxlines=None):
 vals=wrap(value,width,size,bold,mono)
 if maxlines and len(vals)>maxlines:vals=vals[:maxlines];vals[-1]=vals[-1][:-2]+'…'
 for i,line in enumerate(vals):text(im,(xy[0],xy[1]+i*size*spacing),line,size,color,bold,mono)
 return len(vals)*size*spacing
def pill(im,x,y,label,fill='#EAF1FF',color=BLUE,size=25):
 width=int(font(size).getlength(label))+40;box(im,(x,y,x+width,y+46),fill,None,r=12);text(im,(x+20,y+8),label,size,color);return width

def brand(im,x=96,y=48,size=36):
 d=ImageDraw.Draw(im);d.polygon([(x,y+38),(x+38,y),(x+38,y+38)],fill=BLUE)
 text(im,(x+56,y-1),'AgentPair',size,INK,True,en=True)

def base(title,number):
 im=Image.new('RGB',(W,H),BG);brand(im)
 text(im,(1255,58),'Agent 行为可检索 · 过程可追溯',27,MUTED)
 text(im,(96,153),title,58,INK,True)
 text(im,(96,1018),'www.chuhaijian.com',25,MUTED)
 text(im,(1390,1020),'合成示例 · 宣传画面编排' if DEMO_MODE else '本机选定样例 · 宣传画面编排',21,MUTED)
 ImageDraw.Draw(im).line((96,998,1824,998),fill=LINE,width=2)
 text(im,(1780,152),f'{number:02d}',36,'#A1ACBC',mono=True)
 return im

def layer():return Image.new('RGBA',(W,H),(0,0,0,0))
def ease(v):v=max(0,min(1,v));return 1-(1-v)**3
def animate(im,lay,t,delay=0,duration=.65,slide=30):
 a=ease((t-delay)/duration)
 if a<=0:return
 if a<1:
  lay=lay.copy();lay.putalpha(lay.getchannel('A').point(lambda x:int(x*a)))
 im.paste(lay,(0,round((1-a)*slide)),lay)

def arrow(im,a,b,color=BLUE,width=4):
 d=ImageDraw.Draw(im);d.line((a,b),fill=color,width=width)
 dx,dy=b[0]-a[0],b[1]-a[1];n=math.hypot(dx,dy);u,v=dx/n,dy/n
 d.polygon([b,(b[0]-15*u+7*v,b[1]-15*v-7*u),(b[0]-15*u-7*v,b[1]-15*v+7*u)],fill=color)

def clip_shot(im,shot,crop,target):
 image=shot.crop(crop);tw,th=target[2]-target[0],target[3]-target[1]
 image.thumbnail((tw,th),Image.Resampling.LANCZOS)
 px=target[0]+(tw-image.width)//2;py=target[1]+(th-image.height)//2
 im.paste(image,(px,py))
 return (px,py,image.width,image.height)

LAYERS={}
# Opening: a product question grounded in the actual task.
l=layer();text(l,(96,285),'Agent 说完成了。',100,INK,True);text(l,(96,421),'你能核实吗？',110,BLUE,True)
text(l,(102,600),'从一条工具调用，看清任务的来龙去脉。',38,MUTED)
LAYERS['opening']=[l]
# Search: query typing is animated; result cards use selected input metadata.
a=layer();box(a,(96,279,1824,394),'white',LINE,r=20);d=ImageDraw.Draw(a);d.ellipse((135,315,165,345),outline=BLUE,width=4);d.line((162,342,176,356),fill=BLUE,width=4)
text(a,(1453,305),'数据中心',27,MUTED);pill(a,1610,298,'搜索',BLUE,'white',29)
results=[]
for i,item in enumerate(SEARCH['items'][:3]):
 l=layer();y=435+i*140;box(l,(96,y,1246,y+116));text(l,(128,y+19),'VideoGen',33,INK,True,en=True)
 lines(l,(129,y+71),item.get('title') or '工具调用',780,30,INK,maxlines=1)
 text(l,(980,y+23),'示例时间' if DEMO_MODE else '本机记录',25,MUTED)
 pill(l,960,y+64,item.get('application') or 'Agent',size=23)
 results.append(l)
b=layer();box(b,(1300,435,1824,830),'#EDF3FF',None);text(b,(1340,475),str(SEARCH.get('total',len(SEARCH.get('items',[])))),112,BLUE,True,en=True)
text(b,(1450,547),'条示例记录' if DEMO_MODE else '条本机选定记录',31,INK,True);text(b,(1342,660),'按工具名称，定位已上传记录',29,MUTED)
text(b,(1342,720),'提示词 · 参数 · 返回 · 上下文',27,BLUE)
LAYERS['search']=[a,*results,b]
# Request + selected screenshot or synthetic placeholder.
a=layer();box(a,(96,286,870,914));text(a,(132,327),'示例需求' if DEMO_MODE else '本机选定需求',27,MUTED)
lines(a,(132,395),(DATA.get('request') or {}).get('text') or '用户需求见本机选定记录。',660,45,INK,True,maxlines=3)
lines(a,(132,555),'先查看需求，再展开参数与返回，核对产物是否满足要求。',670,32,MUTED,maxlines=4)
pill(a,132,758,DATA.get('application') or 'Agent');pill(a,327,758,'SessionLens')
text(a,(132,837),DATA.get('deviceName') or '设备未标注',27,MUTED,mono=True)
b=layer();box(b,(912,286,1824,914));text(b,(945,327),'示例界面' if DEMO_MODE else '本机选定产品截图',27,MUTED)
clip_shot(b,SHOT1,(247,68,1257,366),(938,402,1796,688))
box(b,(949,421,1781,451),None,BLUE,r=7,width=3)
text(b,(947,764),'哪台设备？哪个 Agent？',42,INK,True)
text(b,(950,837),'来源信息，与调用记录一起查看。',30,MUTED)
LAYERS['request']=[a,b]
# Tool arguments / prompt excerpt with full real raw text saved separately.
a=layer();box(a,(96,280,1240,917));text(a,(135,321),'提交给工具的提示词 · 原文摘录',28,MUTED)
params=(DATA.get('arguments') or {}).get('params') or {};prompt=str(params.get('prompt') or '本次选定记录未包含可读提示词。')
lines(a,(135,399),prompt[:515],1060,30,INK,mono=False,spacing=1.58,maxlines=8)
pill(a,136,821,'完整提示词可在调用详情查看',size=25)
b=layer();text(b,(1306,285),'调用参数',29,MUTED)
for j,(label,value,key) in enumerate([('画面比例',params.get('aspect_ratio','待核对'),'aspect_ratio'),('分辨率',params.get('resolution','待核对'),'resolution'),('生成音频',str(params.get('enable_audio','待核对')),'enable_audio')]):
 y=350+j*154;box(b,(1300,y,1824,y+135));text(b,(1335,y+21),label,29,MUTED);text(b,(1335,y+68),value,43,INK,True);text(b,(1560,y+86),key,21,MUTED,mono=True)
text(b,(1307,845),'函数：DeferExecuteTool',26,BLUE)
LAYERS['parameters']=[a,b]
# Comparison is manually derived from explicit ffprobe evidence; no auto-risk claim.
a=layer();box(a,(96,282,909,913));text(a,(134,326),'用户要求',29,MUTED);text(a,(135,425),metric(REQUESTED),158,BLUE,True,en=True);text(a,(352,511),'秒',51,BLUE)
text(a,(135,650),'协议流程动画',42,INK,True);pill(a,138,784,'按用户要求核对输出规格',size=25)
b=layer();box(b,(965,282,1824,913));text(b,(1005,326),'ffprobe 核验记录',29,MUTED);text(b,(1005,425),metric(OBSERVED),158,AMBER,True,en=True);text(b,(1391,511),'秒',51,AMBER)
box(b,(1005,649,1784,817),'#111E31',None,r=15)
lines(b,(1034,678),f"width={metric(PRESENTATION.get('width'))}   height={metric(PRESENTATION.get('height'))}\nduration={metric(OBSERVED)}",720,32,'#DCE7F5',mono=True,spacing=1.75)
text(b,(1007,851),'调用状态与文件核验分别查看',29,MUTED)
LAYERS['verification']=[a,b]
# Explicitly linked record chain.
a=layer();pill(a,98,281,DATA.get('application') or 'Agent');pill(a,302,281,'SessionLens');text(a,(533,292),DATA.get('deviceName') or '设备未标注',27,MUTED,mono=True)
xs=[281,706,1131,1556];labels=['用户提问','调用 VideoGen','工具返回','文件核验'];times=['步骤 1','步骤 2','步骤 3','步骤 4']
for i,x in enumerate(xs):
 box(a,(x-175,563,x+175,786));text(a,(x-133,601),labels[i],32,INK,True);text(a,(x-133,658),times[i],27,MUTED,mono=True)
 lines(a,(x-133,711),['需求与输出规格','提示词 + 参数','状态与输出文件','时长与分辨率核验'][i],284,25,BLUE,maxlines=2)
text(a,(99,863),'沿着已记录的关联，回看每一步的内容。',38,INK)
LAYERS['chain']=[a]
# Closing.
a=layer();text(a,(96,316),'AgentPair',126,INK,True,en=True);text(a,(96,505),'让 Agent 的每次行动，有据可查。',60,BLUE,True)
text(a,(98,652),'www.chuhaijian.com',62,INK,True,en=True)
box(a,(98,776,965,880),'#EAF1FF',None);text(a,(130,800),'打开数据中心，搜索',31,MUTED);text(a,(495,800),'tool="VideoGen"',35,BLUE,mono=True)
LAYERS['closing']=[a]


def subtitle(im,scene,t):
 parts=[p.strip() for p in re.split('[。！？]',scene['voice']) if p.strip()]
 index=min(len(parts)-1,int(max(0,t)/scene['duration']*len(parts)));caption=parts[index]
 d=ImageDraw.Draw(im);f=font(35);width=f.getlength(caption)
 if width>1710:caption=caption[:47]+'…';width=f.getlength(caption)
 text(im,((W-width)/2,947),caption,35,INK)

def render(index,t):
 scene=SCENES[index];name=scene['id'];im=base(scene['title'] if name not in ('opening','closing') else '',index+1)
 for j,l in enumerate(LAYERS[name]):animate(im,l,t,delay=j*.15 if name!='search' else [0,1.65,1.85,2.05,2.2][j])
 if name=='search':
  query='tool="VideoGen"';visible=query[:min(len(query),int(max(0,t-.45)*16))]
  text(im,(193,316),visible,42,INK,mono=True)
  if t<1.8 and int(t*3)%2==0:text(im,(193+font(42,mono=True).getlength(visible),312),'|',42,BLUE,mono=True)
 if name=='opening' and t>.5:
  for j,(x,title) in enumerate([(155,'需求'),(565,'Agent'),(975,'工具'),(1385,'返回')]):
   box(im,(x,762,x+270,872),'white',LINE,r=18);text(im,(x+80,796),title,38,INK,True)
   if j<3:
    arrow(im,(x+279,817),(x+401,817),'#C5D3EB',3)
    p=(t/2-j*.13)%1;px=x+279+p*111
    ImageDraw.Draw(im).ellipse((px-6,811,px+6,823),fill=BLUE)
 if name=='chain':
  xs=[281,706,1131,1556];stage=min(3,int(max(0,t-.8)/1.15))
  for j,x in enumerate(xs):
   color=BLUE if j<=stage else '#CAD4E2';ImageDraw.Draw(im).ellipse((x-32,427,x+32,491),fill=color)
   text(im,(x-11,438),str(j+1),30,'white',True)
   if j<3:
    arrow(im,(x+38,459),(xs[j+1]-38,459),BLUE if j<stage else '#CAD4E2',4)
    if j==stage:
     p=((t-.8)%1.15)/1.15;px=x+40+p*340
     ImageDraw.Draw(im).ellipse((px-8,451,px+8,467),fill=BLUE)
 subtitle(im,scene,t)
 # restrained transitions; no strobing.
 fade=ease(t/.32)*ease((scene['duration']-t)/.25)
 if fade<1:im=Image.blend(Image.new('RGB',(W,H),BG),im,fade)
 return im

def create_music(duration):
 sr=48000;n=int(duration*sr);tt=np.arange(n,dtype=np.float64)/sr
 audio=np.zeros(n,dtype=np.float64)
 for basefreq in [146.83,220,293.66,440]:
  audio+=(np.sin(2*np.pi*basefreq*tt)+.18*np.sin(2*np.pi*basefreq*2*tt))*.012/4
 env=np.minimum(1,tt/2)*np.minimum(1,(duration-tt)/3)
 audio*=env*(.83+.17*np.sin(2*np.pi*.12*tt))
 data=(np.clip(audio,-1,1)*32767).astype('<i2')
 with wave.open(str(BUILD/'music.wav'),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(sr);f.writeframes(data.tobytes())

def stamp(v):
 ms=round(v*1000);return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'
def main():
 args=ARGS
 if args.preview:
  thumbs=[]
  for i,s in enumerate(SCENES):
   im=render(i,min(3.,s['duration']/2));im.save(BUILD/f'preview-{s["id"]}.png');thumbs.append(im.resize((640,360)))
  sheet=Image.new('RGB',(1280,1440),'#E8ECF3')
  for i,img in enumerate(thumbs):sheet.paste(img,((i%2)*640,(i//2)*360))
  sheet.save(BASE/'storyboard-preview.jpg',quality=92);print('previews ready');return
 video=BUILD/'silent.mp4'
 p=subprocess.Popen(['/usr/local/bin/ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20','-pix_fmt','yuv420p',str(video)],stdin=subprocess.PIPE)
 elapsed=0;subs=[]
 try:
  for i,s in enumerate(SCENES):
   count=round(s['duration']*FPS);s['duration']=count/FPS
   for n in range(count):p.stdin.write(render(i,n/FPS).tobytes())
   subs.append(f'{i+1}\n{stamp(elapsed)} --> {stamp(elapsed+s["duration"])}\n{s["voice"]}\n');elapsed+=s['duration'];print(s['id'],'rendered',flush=True)
  p.stdin.close();assert p.wait()==0
 except Exception:
  p.kill();raise
 (BASE/'AgentPair-VideoGen.srt').write_text('\n'.join(subs),encoding='utf-8')
 create_music(elapsed)
 command=['/usr/local/bin/ffmpeg','-y','-loglevel','error','-i',str(video)]
 for s in SCENES:command+=['-i',s['audio']]
 command+=['-i',str(BUILD/'music.wav')]
 filt=[]
 for i,s in enumerate(SCENES):filt.append(f'[{i+1}:a]aresample=48000,apad,atrim=duration={s["duration"]},asetpts=PTS-STARTPTS[a{i}]')
 filt.append(''.join(f'[a{i}]' for i in range(len(SCENES)))+f'concat=n={len(SCENES)}:v=0:a=1[narr]')
 filt.append(f'[narr]volume=1.2[n];[n][{len(SCENES)+1}:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]')
 command+=['-filter_complex',';'.join(filt),'-map','0:v','-map','[a]','-c:v','copy','-c:a','aac','-b:a','192k','-t',str(elapsed),'-movflags','+faststart',str(BASE/'AgentPair-VideoGen-宣传片.mp4')]
 subprocess.run(command,check=True)
 cover=render(6,2.5);cover.save(BASE/'AgentPair-VideoGen-封面.jpg',quality=95)
 print('complete',elapsed,flush=True)
if __name__=='__main__':main()
