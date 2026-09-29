"""Bounded public GET and IP-registration collection; no scanning or auth."""
import hashlib
import datetime
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

TARGET = 'http://102.68.79.149/'
LIMIT = 262144


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def read_url(url, limit=LIMIT):
    req = urllib.request.Request(url, headers={'User-Agent':'AgentPair-PublicEvidence/0.1','Accept-Encoding':'identity'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(req, timeout=12)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(limit+1)
        return response.status, response.headers, data[:limit], len(data)>limit


class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.assets=[]; self.title=''; self.in_title=False; self.generator=None
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='title': self.in_title=True
        if tag=='meta' and attrs.get('name','').lower()=='generator': self.generator=attrs.get('content','')[:160]
        if tag=='script' and attrs.get('src'): self.assets.append(attrs['src'])
        if tag=='link' and attrs.get('rel')=='stylesheet' and attrs.get('href'): self.assets.append(attrs['href'])
    def handle_endtag(self, tag):
        if tag=='title': self.in_title=False
    def handle_data(self, data):
        if self.in_title: self.title=(self.title+data)[:200]


def validate_target(target):
    if not isinstance(target,str): raise ValueError('Invalid target')
    parts=urllib.parse.urlsplit(target)
    if parts.scheme not in ('http','https') or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Only plain public IP HTTP(S) URLs are supported')
    ip=ipaddress.ip_address(parts.hostname or '')
    if ip.version!=4 or not ip.is_global or parts.port not in (None,80,443):
        raise ValueError('Only globally routable IPv4 and standard web ports are supported')
    if (parts.scheme=='http' and parts.port==443) or (parts.scheme=='https' and parts.port==80):
        raise ValueError('Nonstandard scheme/port')
    return urllib.parse.urlunsplit((parts.scheme,parts.netloc,parts.path or '/','',''))


def allowed_asset(value,target=TARGET):
    target=validate_target(target)
    url=urllib.parse.urljoin(target,value)
    parts=urllib.parse.urlsplit(url)
    origin=urllib.parse.urlsplit(target)
    if (parts.scheme,parts.hostname,parts.port or (443 if parts.scheme=='https' else 80))!=(origin.scheme,origin.hostname,origin.port or (443 if origin.scheme=='https' else 80)): return None
    if parts.username or parts.password or parts.query: return None
    return urllib.parse.urlunsplit((parts.scheme,parts.netloc,parts.path,'',''))


def markers(text):
    patterns={'nextjs_marker':r'__NEXT_DATA__|/_next/', 'wordpress_marker':r'wp-content/|wp-includes/',
              'vue_marker':r'data-v-[0-9a-f]+|__VUE__', 'react_marker':r'__REACT_DEVTOOLS|react-dom',
              'jquery_marker':r'jQuery v\d|jquery[-.]\d', 'vite_marker':r'/@vite/client|vite/modulepreload'}
    return [name for name,pattern in patterns.items() if re.search(pattern,text,re.I)]


def collect(target=TARGET):
    target=validate_target(target)
    evidence=[]
    def add(kind,fields):
        evidence.append(dict(ref=f'W{len(evidence)+1:03d}',kind=kind,
            collectedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),**fields))
    try:
        status,headers,body,truncated=read_url(target)
        text=body.decode('utf-8','replace'); page=Page(); page.feed(text)
        selected={k:headers[k][:200] for k in ('Server','X-Powered-By','Content-Type','Location') if headers.get(k)}
        cookies=[value.split('=',1)[0][:80] for value in headers.get_all('Set-Cookie',[])]
        add('http_root',{'url':target,'status':status,'headers':selected,'cookieNames':cookies,
                         'title':page.title,'generator':page.generator,'markers':markers(text),
                         'bodySHA256':hashlib.sha256(body).hexdigest(),'truncated':truncated})
        assets=[]
        for value in page.assets:
            url=allowed_asset(value,target)
            if url and url not in assets: assets.append(url)
        for url in assets[:2]:
            try:
                s,h,b,t=read_url(url)
                add('public_asset',{'url':url,'status':s,'contentType':h.get('Content-Type','')[:100],
                                    'markers':markers(b.decode('utf-8','replace')),
                                    'publicPathHints':sorted(set(re.findall(r'["\'](/(?:api|rest|webfig|login|js|assets)/[A-Za-z0-9_./-]{1,100})["\']',b.decode('utf-8','replace'))))[:12],
                                    'bodySHA256':hashlib.sha256(b).hexdigest(),'truncated':t})
            except Exception as error: add('asset_error',{'errorType':type(error).__name__})
    except Exception as error: add('http_error',{'errorType':type(error).__name__})
    try:
        status,_,body,_=read_url('https://data.iana.org/rdap/ipv4.json',1048576)
        if status!=200: raise ValueError('Bootstrap unavailable')
        bootstrap=json.loads(body); ip=ipaddress.ip_address(urllib.parse.urlsplit(target).hostname); base=None
        for ranges,urls in bootstrap['services']:
            if any(ip in ipaddress.ip_network(cidr) for cidr in ranges):
                base=next((u for u in urls if u.startswith('https://')),None); break
        if not base: raise ValueError('No HTTPS registry service')
        url=base.rstrip('/')+'/ip/'+str(ip)
        status,_,body,truncated=read_url(url,1048576)
        if status!=200 or truncated: raise ValueError('Registry query unavailable')
        record=json.loads(body)
        add('ip_registration',{'registryURL':url,'name':str(record.get('name',''))[:160],
                               'country':str(record.get('country',''))[:8],
                               'startAddress':record.get('startAddress'),'endAddress':record.get('endAddress'),
                               'note':'Registration country is not proof of physical server location.'})
    except Exception as error: add('registration_error',{'errorType':type(error).__name__})
    try:
        ip=urllib.parse.urlsplit(target).hostname
        url='https://stat.ripe.net/data/network-info/data.json?resource='+ip
        status,_,body,truncated=read_url(url,1048576)
        if status!=200 or truncated: raise ValueError('Routing source unavailable')
        record=json.loads(body)
        if record.get('status')!='ok': raise ValueError('Routing query failed')
        data=record['data']
        add('routing_intelligence',{'sourceURL':url,'prefix':data.get('prefix'),
            'asns':data.get('asns',[])[:8],'queryTime':record.get('time'),
            'note':'RIPE RIS routing observation, not evidence of ownership or malicious activity.'})
    except Exception as error: add('routing_error',{'errorType':type(error).__name__})
    return {'target':target,'evidence':evidence,'limits':{'rootGET':1,'assetGETMax':2,'portScanning':False,
           'authentication':False,'redirectsFollowed':False},
           'threatIntelligence':{'status':'not_configured','note':'No paid reputation, passive DNS or historical malicious-activity source is configured. No result is not a clean verdict.'},
           'limitations':['Headers and HTML markers may be forged or represent a proxy.',
                          'No private backend source or authenticated data was accessed.',
                          'IP registry allocation is not physical geolocation; city and origin server may remain unknown.']}
