"""Bounded public GET and IP-registration collection; no scanning or auth."""
import hashlib
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


def allowed_asset(value):
    url=urllib.parse.urljoin(TARGET,value)
    parts=urllib.parse.urlsplit(url)
    if parts.scheme!='http' or parts.hostname!='102.68.79.149' or parts.port not in (None,80): return None
    if parts.username or parts.password or parts.query: return None
    return urllib.parse.urlunsplit((parts.scheme,parts.netloc,parts.path,'',''))


def markers(text):
    patterns={'nextjs_marker':r'__NEXT_DATA__|/_next/', 'wordpress_marker':r'wp-content/|wp-includes/',
              'vue_marker':r'data-v-[0-9a-f]+|__VUE__', 'react_marker':r'__REACT_DEVTOOLS|react-dom',
              'jquery_marker':r'jQuery v\d|jquery[-.]\d', 'vite_marker':r'/@vite/client|vite/modulepreload'}
    return [name for name,pattern in patterns.items() if re.search(pattern,text,re.I)]


def collect():
    evidence=[]
    def add(kind,fields):
        evidence.append(dict(ref=f'W{len(evidence)+1:03d}',kind=kind,**fields))
    try:
        status,headers,body,truncated=read_url(TARGET)
        text=body.decode('utf-8','replace'); page=Page(); page.feed(text)
        selected={k:headers[k][:200] for k in ('Server','X-Powered-By','Content-Type','Location') if headers.get(k)}
        cookies=[value.split('=',1)[0][:80] for value in headers.get_all('Set-Cookie',[])]
        add('http_root',{'url':TARGET,'status':status,'headers':selected,'cookieNames':cookies,
                         'title':page.title,'generator':page.generator,'markers':markers(text),
                         'bodySHA256':hashlib.sha256(body).hexdigest(),'truncated':truncated})
        assets=[]
        for value in page.assets:
            url=allowed_asset(value)
            if url and url not in assets: assets.append(url)
        for url in assets[:2]:
            try:
                s,h,b,t=read_url(url)
                add('public_asset',{'url':url,'status':s,'contentType':h.get('Content-Type','')[:100],
                                    'markers':markers(b.decode('utf-8','replace')),'bodySHA256':hashlib.sha256(b).hexdigest(),'truncated':t})
            except Exception as error: add('asset_error',{'errorType':type(error).__name__})
    except Exception as error: add('http_error',{'errorType':type(error).__name__})
    try:
        status,_,body,_=read_url('https://data.iana.org/rdap/ipv4.json',1048576)
        if status!=200: raise ValueError('Bootstrap unavailable')
        bootstrap=json.loads(body); ip=ipaddress.ip_address('102.68.79.149'); base=None
        for ranges,urls in bootstrap['services']:
            if any(ip in ipaddress.ip_network(cidr) for cidr in ranges):
                base=next((u for u in urls if u.startswith('https://')),None); break
        if not base: raise ValueError('No HTTPS registry service')
        url=base.rstrip('/')+'/ip/102.68.79.149'
        status,_,body,truncated=read_url(url,1048576)
        if status!=200 or truncated: raise ValueError('Registry query unavailable')
        record=json.loads(body)
        add('ip_registration',{'registryURL':url,'name':str(record.get('name',''))[:160],
                               'country':str(record.get('country',''))[:8],
                               'startAddress':record.get('startAddress'),'endAddress':record.get('endAddress'),
                               'note':'Registration country is not proof of physical server location.'})
    except Exception as error: add('registration_error',{'errorType':type(error).__name__})
    return {'target':TARGET,'evidence':evidence,'limits':{'rootGET':1,'assetGETMax':2,'portScanning':False,
           'authentication':False,'redirectsFollowed':False},
           'limitations':['Headers and HTML markers may be forged or represent a proxy.',
                          'No private backend source or authenticated data was accessed.',
                          'IP registry allocation is not physical geolocation; city and origin server may remain unknown.']}
