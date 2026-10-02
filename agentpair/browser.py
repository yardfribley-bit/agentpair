"""WebLens live API bridge. Only enabled on a provisioned, isolated Driver.

The deployment must enforce browser egress separately: URL checks here do not
control subresources, redirects or DNS rebinding inside the browser engine.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
from html.parser import HTMLParser


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.hidden+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
    def handle_data(self,text):
        if not self.hidden: self.parts.append(text)


class WebLens:
    def __init__(self, config=None, request=None):
        if config is None:
            if Path('/etc/agentpair-driver').read_text().strip()!='isolated-driver':
                raise RuntimeError('Browser must run on isolated Driver')
            config=json.loads(Path('/etc/agentpair-browser.json').read_text())
        self.config=config
        # Explicit deployment readiness, never enabled by an LLM or task payload.
        if not config.get('egressIsolated'): raise RuntimeError('Browser egress isolation not configured')
        base=config.get('endpoint','http://127.0.0.1:8081')
        p=urlsplit(base)
        if p.scheme!='http' or p.hostname!='127.0.0.1' or p.username or p.path not in ('','/'):
            raise ValueError('WebLens endpoint must be Driver loopback')
        self.base=base.rstrip('/'); self.request=request or self._request; self.url=None

    def _request(self, op, payload):
        req=Request(self.base+'/api/live/'+op,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with build_opener(ProxyHandler({})).open(req,timeout=35) as response:
            raw=response.read(2_000_001)
        if len(raw)>2_000_000: raise ValueError('Browser response too large')
        return json.loads(raw)

    def step(self, action):
        op=action.get('op')
        if op=='open':
            url=action.get('url',''); p=urlsplit(url)
            origin=f'{p.scheme}://{p.netloc}'
            if p.scheme not in ('http','https') or p.username or p.password or origin not in self.config.get('allowedOrigins',[]):
                raise ValueError('URL origin not approved for this Driver')
            if self.url:self.close()
            self.url=url  # Also close partially opened sessions on errors.
            self.request('open',{'url':url})
            out=self.request('snapshot',{'url':url,'findKeys':False})
        elif op in ('snapshot','scroll','interact'):
            if not self.url:raise ValueError('Open a page first')
            payload={'url':self.url}
            if op=='scroll':
                dy=action.get('dy',400)
                if type(dy)!=int or abs(dy)>2000:raise ValueError('Invalid scroll distance')
                payload.update(dx=0,dy=dy)
            if op=='interact':
                selector=action.get('selector')
                # Exact pre-approved selectors only; new actions require operator approval.
                if selector not in self.config.get('allowedSelectors',{}).get(self.url,[]):
                    raise ValueError('Interaction requires explicit selector approval')
                payload['selector']=selector
            out=self.request(op,payload)
            if op=='scroll':out=self.request('snapshot',{'url':self.url,'findKeys':False})
        else:raise ValueError('Unsupported browser operation')
        parser=Text();parser.feed(out.get('html',''))
        return {'url':out.get('url',self.url),'title':str(out.get('title',''))[:300],
                'text':' '.join(parser.parts)[:12000],
                'shotAvailable':bool(out.get('shotAvailable',False)),
                'note':'DOM evidence only; no screenshot or visual acceptance claimed.'}

    def close(self):
        if self.url:
            url=self.url;self.url=None
            self.request('close',{'url':url})
