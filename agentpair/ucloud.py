"""Narrow UCloud API client; credentials never enter model context."""
import hashlib
import json
import urllib.request


class CloudRejected(RuntimeError):
    """A definite provider rejection, retaining only a non-secret numeric code."""
    def __init__(self, action, code):
        self.code = code if type(code) is int else None
        super().__init__('UCloud ' + action + ' returned ' + str(self.code))


class UCloudClient:
    def __init__(self, public_key, private_key, project_id, region='cn-bj2', transport=None):
        self.public_key=public_key
        self.private_key=private_key
        self.project_id=project_id
        self.region=region
        self.transport=transport or self._http

    @staticmethod
    def _http(payload):
        request=urllib.request.Request('https://api.ucloud.cn/',
            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=25) as response:
            return json.load(response)

    def call(self, action, **extra):
        if action not in {'DescribeUHostInstance','DescribeImage','GetUHostInstancePrice','GetUHostInstanceVncInfo',
                         'GetEIPPrice','CreateUHostInstance','StopUHostInstance',
                         'TerminateUHostInstance','DescribeEIP','CreateFirewall',
                         'DescribeFirewall','DeleteFirewall','GrantFirewall','GetUHostUpgradePrice',
                         'ResizeUHostInstance','StartUHostInstance','ReinstallUHostInstance','ResetUHostInstancePassword'}:
            raise ValueError('Cloud action not allowed')
        payload={'Action':action,'PublicKey':self.public_key,'ProjectId':self.project_id,
                 'Region':self.region,**extra}
        def value(v): return str(v).lower() if isinstance(v,bool) else str(v)
        raw=''.join(k+value(payload[k]) for k in sorted(payload))+self.private_key
        payload['Signature']=hashlib.sha1(raw.encode()).hexdigest()
        result=self.transport(payload)
        if result.get('RetCode')!=0:
            raise CloudRejected(action, result.get('RetCode'))
        return result
