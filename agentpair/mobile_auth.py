"""Short-lived login handoffs. Codes stay in RAM and never enter task records."""
import secrets
import threading
import time
import re
from urllib.parse import urlsplit


class MobileAuth:
    def __init__(self, devices, clock=time.time):
        self.devices, self.clock = devices, clock
        self.lock = threading.RLock()
        self.items = {}

    def _prune(self):
        for key in list(self.items):
            if self.items[key]['expiresAt'] <= self.clock(): del self.items[key]

    @staticmethod
    def public(item):
        return {k:v for k,v in item.items() if k not in ('code','owner','consumerToken')}

    def create(self, owner, device_id, origin, brand, task_id):
        device=self.devices.get(device_id,owner)
        if not device or device.get('snapshot',{}).get('os')!='Android':
            raise PermissionError('请选择本人已接入的 Android 设备')
        url=urlsplit(origin or '')
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            raise ValueError('登录网站必须是 HTTPS 来源地址')
        if not isinstance(brand,str) or not 3<=len(brand.strip())<=60:
            raise ValueError('需要短信中的服务签名关键词')
        with self.lock:
            self._prune()
            if any(i['deviceId']==device_id for i in self.items.values()):
                raise ValueError('该手机已有登录请求，请先取消或等待过期')
            if len(self.items)>=100:raise ValueError('登录请求过多')
            item={'id':secrets.token_urlsafe(24),'owner':owner,'deviceId':device_id,
                  'origin':origin.rstrip('/'),'brand':brand.strip(),'taskId':task_id,
                  'createdAt':self.clock(),'expiresAt':self.clock()+180,'state':'waiting',
                  'consumerToken':secrets.token_urlsafe(32)}
            self.items[item['id']]=item
            return {**self.public(item),'consumerToken':item['consumerToken']}

    def _identity(self, token):
        with self.devices.connect() as db:return dict(self.devices._device_by_token(db,token))

    def pending(self, token):
        device=self._identity(token)
        with self.lock:
            self._prune()
            return [self.public(i) for i in self.items.values() if i['deviceId']==device['id'] and i['state']=='waiting']

    def submit(self, token, challenge_id, code):
        device=self._identity(token)
        if not isinstance(code,str) or not re.fullmatch(r'[0-9]{4,8}',code):raise ValueError('Invalid code')
        with self.lock:
            self._prune();item=self.items.get(challenge_id)
            if not item or item['deviceId']!=device['id']:raise PermissionError('Login request unavailable')
            if item['state']!='waiting':raise ValueError('Code already submitted')
            item.update(code=code,state='received')
            return {'accepted':True,'state':'received'}

    def status(self, owner, challenge_id):
        with self.lock:
            self._prune();item=self.items.get(challenge_id)
            if not item or item['owner']!=owner:raise KeyError('Login request not found')
            return self.public(item)

    def cancel(self, owner, challenge_id):
        with self.lock:
            self.status(owner,challenge_id);del self.items[challenge_id]
            return {'cancelled':True}

    def consume(self, challenge_id, token, origin):
        with self.lock:
            self._prune();item=self.items.get(challenge_id)
            if not item or not secrets.compare_digest(item['consumerToken'],token or '') or origin!=item['origin']:
                raise PermissionError('Login consumer not authorized')
            # Revocation immediately disables both submission and consumption.
            if not self.devices.get(item['deviceId'],item['owner']):raise PermissionError('Device revoked')
            if item['state']!='received':return {'state':'waiting'}
            code=item['code'];del self.items[challenge_id]
            return {'state':'consumed','code':code,'origin':origin}
