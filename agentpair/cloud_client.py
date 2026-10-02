"""Shared HTTP client for administrator automation; no direct provider/SSH calls."""
import http.cookiejar
import json
import urllib.request
from urllib.parse import urlparse
import uuid


class CloudClient:
    def __init__(self, base_url):
        parsed=urlparse(base_url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Navigator HTTPS URL required')
        self.base=base_url.rstrip('/')
        self.csrf=''
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, data=None):
        if not path.startswith('/api/'):raise ValueError('API path required')
        headers={'Accept':'application/json'}
        body=None
        if data is not None:
            body=json.dumps(data).encode()
            headers.update({'Content-Type':'application/json','X-CSRF-Token':self.csrf,'Origin':self.base})
        request=urllib.request.Request(self.base+path,data=body,headers=headers)
        with self.opener.open(request,timeout=90) as response:
            return json.load(response)

    def login(self, username, password):
        result=self.request('/api/login',{'username':username,'password':password})
        if result.get('role')!='admin':raise PermissionError('Administrator required')
        self.csrf=result['csrf']

    def quote(self, system):return self.request('/api/cloud/quote',{'system':system})
    def machines(self):return self.request('/api/cloud/machines')
    def create(self, system, max_hourly_cny, request_id=None):
        # Caller must retain this ID for retries after a timeout.
        if not request_id:raise ValueError('Retain a request ID before creating a machine')
        return self.request('/api/cloud/create',{'system':system,'maxHourlyCNY':max_hourly_cny,'requestId':request_id})
    @staticmethod
    def request_id():return str(uuid.uuid4())
    def start(self, lease_id):return self.request('/api/cloud/start',{'leaseId':lease_id})
    def check_login(self, lease_id):return self.request('/api/cloud/machines/'+lease_id+'/login')
    def install(self, lease_id, software_id):
        return self.request('/api/cloud/install',{'leaseId':lease_id,'softwareId':software_id})
    def operation(self, operation_id):return self.request('/api/cloud/operations/'+operation_id)
