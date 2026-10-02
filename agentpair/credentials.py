"""Server-only credentials. No secret values in status responses or task data."""
import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import re
import uuid
from urllib.parse import urlsplit

class CredentialStore:
    def __init__(self,directory):
        self.root=Path(directory);self.lock=threading.Lock()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.root,0o700)
    def status(self):
        with self.lock:
            return {'items':[self.public(json.loads(p.read_text())) for p in sorted(self.root.glob('service-*.json'))]}
    @staticmethod
    def public(data):
        return {**{k:data.get(k,'') for k in ('id','name','purpose','endpoint','docsUrl','authType','updatedAt')},
                'fields':list(data['secrets']),'configured':True,'status':'saved_unverified'}
    def save(self,request):
        data={}
        for key,limit in [('name',100),('purpose',1500),('endpoint',1000),('docsUrl',1000),('authType',40)]:
            value=request.get(key,'')
            if not isinstance(value,str) or len(value)>limit:raise ValueError('Invalid metadata')
            data[key]=value.strip()
        if not data['name'] or data['authType'] not in ('api_key','bearer','basic','custom'):raise ValueError('Invalid service')
        for key in ('endpoint','docsUrl'):
            if data[key]:
                url=urlsplit(data[key])
                if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:raise ValueError('Use HTTPS URL without credentials or query')
        incoming=request.get('secrets',{})
        if not isinstance(incoming,dict) or not 1<=len(incoming)<=20:raise ValueError('Credential fields required')
        for key,value in incoming.items():
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}',key) or not isinstance(value,str) or len(value)>4096:raise ValueError('Invalid credential field')
        identifier=request.get('id') or uuid.uuid4().hex
        if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('Invalid id')
        with self.lock:
            target=self.root/('service-'+identifier+'.json')
            old=json.loads(target.read_text()) if target.exists() else None
            if request.get('id') and old is None:raise ValueError('Unknown service')
            values={key:value or (old or {}).get('secrets',{}).get(key,'') for key,value in incoming.items()}
            if not all(values.values()):raise ValueError('New fields require values')
            data.update(id=identifier,secrets=values,updatedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
            fd,name=tempfile.mkstemp(dir=self.root,prefix='.service-')
            try:
                with os.fdopen(fd,'w') as stream:
                    json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
                os.replace(name,target)
            finally:
                if os.path.exists(name):os.unlink(name)
        return self.public(data)
