"""Trusted browser-side helper; never register its code-bearing response as an LLM tool."""
from urllib.parse import urlsplit
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def fill_pending_code(platform, challenge_id, consumer_token, origin, browser):
    """browser.current_url() and browser.fill_login_code(code) must stay local to Driver.

    Consumer token is handed to the trusted task runner, not a prompt or event.
    Return only a redacted state. The runner separately verifies login success.
    """
    current=urlsplit(browser.current_url())
    if f'{current.scheme}://{current.netloc}'!=origin:raise PermissionError('Browser left the login origin')
    if urlsplit(platform).scheme!='https':raise ValueError('HTTPS required')
    req=Request(platform.rstrip('/')+'/api/mobile-auth/consume',data=json.dumps({'id':challenge_id,'origin':origin}).encode(),
                headers={'Content-Type':'application/json','Authorization':'Bearer '+consumer_token})
    with build_opener(NoRedirect).open(req,timeout=10) as response:reply=json.loads(response.read(4096))
    if reply['state']=='waiting':return {'state':'waiting_phone'}
    current=urlsplit(browser.current_url())
    if f'{current.scheme}://{current.netloc}'!=origin:raise PermissionError('Browser left the login origin')
    try:browser.fill_login_code(reply['code'])
    finally:reply.clear()
    return {'state':'code_filled','loginVerified':False}
