"""Narrow test-host VNC diagnostics. Credentials stay in memory, never stdout."""
import json
import shlex
from pathlib import Path
import socket
import struct
import sys
import time
from Crypto.Cipher import DES
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy_persistent_navigator import ssh

class Console:
    def __init__(self, host_id='uhost-1vx2hskj8swi', lease_id=None):
        code="import sys,json;from pathlib import Path;sys.path.insert(0,'/opt/agentpair');from agentpair.ucloud import UCloudClient;c=json.loads(Path('/etc/agentpair/private.json').read_text())['cloud'];a=UCloudClient(c['publicKey'],c['privateKey'],c['projectId'],c['region']);print(json.dumps(a.call('GetUHostInstanceVncInfo',UHostId='uhost-1vx0bfyivbum',Zone=c['zone'])))"
        if lease_id:
            import re
            from datetime import datetime,timezone
            if not re.fullmatch('[0-9a-f]{24}',lease_id):raise ValueError('Invalid lease')
            lease=json.loads(ssh('cat /var/lib/agentpair/leases/'+lease_id+'.json'))
            if (lease.get('hostId')!=host_id or lease.get('platform')!='windows' or lease.get('state')!='active'
                or datetime.fromisoformat(lease['expiresAt'])<=datetime.now(timezone.utc)):
                raise ValueError('Console requires an active exact Windows lease')
            code=code.replace("Zone=c['zone']","Zone="+repr(lease['zone']))
        elif host_id not in ('uhost-1vx2hskj8swi','uhost-1vxy9wj3o4rs','uhost-1vyh19xby6eq'):raise ValueError('Unexpected test host')
        code=code.replace('uhost-1vx0bfyivbum',host_id)
        if host_id in ('uhost-1vxy9wj3o4rs','uhost-1vyh19xby6eq'):code=code.replace("Zone=c['zone']","Zone='cn-bj2-03'")
        info=json.loads(ssh('python3 -c '+shlex.quote(code)))
        if info.get('UHostId')!=host_id:raise RuntimeError('Wrong test host')
        self.sock=socket.create_connection((info['VncIP'],int(info['VncPort'])),timeout=20)
        self.sock.settimeout(25)
        version=self.read(12)
        if not version.startswith(b'RFB '):raise RuntimeError('Not an RFB endpoint')
        self.sock.sendall(b'RFB 003.008\n')
        count=self.read(1)[0];types=self.read(count)
        if 2 not in types:raise RuntimeError('VNC password security unavailable')
        self.sock.sendall(b'\x02');challenge=self.read(16)
        key=info['VncPassword'].encode()[:8].ljust(8,b'\0')
        key=bytes(int(format(x,'08b')[::-1],2) for x in key)
        self.sock.sendall(DES.new(key,DES.MODE_ECB).encrypt(challenge))
        if struct.unpack('>I',self.read(4))[0]:raise RuntimeError('VNC authentication rejected')
        self.sock.sendall(b'\x01')
        self.w,self.h=struct.unpack('>HH',self.read(4));self.read(16)
        self.read(struct.unpack('>I',self.read(4))[0])
        # Request canonical 32-bit little-endian true colour and raw rectangles.
        self.sock.sendall(b'\0\0\0\0'+struct.pack('>BBBBHHHBBBxxx',32,24,0,1,255,255,255,16,8,0))
        self.sock.sendall(struct.pack('>BBHi',2,0,1,0))
    def read(self,n):
        data=b''
        while len(data)<n:
            chunk=self.sock.recv(n-len(data))
            if not chunk:raise RuntimeError('VNC disconnected')
            data+=chunk
        return data
    def key(self,key,down):self.sock.sendall(struct.pack('>BBHI',4,int(down),0,key))
    def click(self,x,y):
        if not (0<=x<self.w and 0<=y<self.h):raise ValueError('Click outside screen')
        self.sock.sendall(struct.pack('>BBHH',5,1,x,y));time.sleep(.08)
        self.sock.sendall(struct.pack('>BBHH',5,0,x,y))
    def press(self,*keys):
        for k in keys:self.key(k,True);time.sleep(.04)
        time.sleep(.04)
        for k in reversed(keys):self.key(k,False);time.sleep(.04)
    def type(self,text):
        # UCloud Windows VNC maps uppercase keysyms itself, but punctuation
        # needs an explicit Shift+physical-key sequence. Shift+lowercase does
        # NOT produce uppercase here. Verified against visible non-secret text.
        shifted=dict(zip('~!@#$%^&*()_+{}|:"<>?', '`1234567890-=[]\\;\',./'))
        def tap(key):
            self.key(key,True);time.sleep(.012);self.key(key,False);time.sleep(.012)
        # Do not toggle Caps Lock: its initial guest state is unknown, and
        # this provider already maps letter case from the keysym. Validated
        # by actual Administrator authentication on the 2026-10-01 trial.
        for c in text:
            if c in shifted:
                self.key(0xffe1,True);time.sleep(.012);tap(ord(shifted[c]));self.key(0xffe1,False)
            else:tap(ord(c))
            time.sleep(.012)
    def command(self, source):
        # Hex transport is case-insensitive even when guest Caps Lock cannot
        # be synchronized through this VNC provider. Decode on the test host.
        payload=source.encode('utf-8').hex()
        wrapper=("$h='"+payload+"';$b=@();for($i=0;$i -lt $h.length;$i+=2)"
                 "{$b+=[convert]::tobyte($h.substring($i,2),16)};"
                 "iex([text.encoding]::utf8.getstring([byte[]]$b))")
        self.type(wrapper);self.press(0xff0d)
    def capture(self,path):
        self.sock.sendall(struct.pack('>BBHHHH',3,0,0,0,self.w,self.h))
        image=Image.new('RGB',(self.w,self.h))
        while True:
            message=self.read(1)[0]
            if message==0:
                self.read(1);count=struct.unpack('>H',self.read(2))[0]
                for _ in range(count):
                    x,y,w,h,encoding=struct.unpack('>HHHHi',self.read(12))
                    if encoding!=0:raise RuntimeError('Unexpected VNC encoding')
                    patch=Image.frombytes('RGB',(w,h),self.read(w*h*4),'raw','BGRX')
                    image.paste(patch,(x,y))
                if count:image.save(path);return
            elif message==2:continue
            elif message==3:
                self.read(3);self.read(struct.unpack('>I',self.read(4))[0])
            else:raise RuntimeError('Unexpected VNC server message')

if __name__=='__main__':
    c=Console()
    try:c.capture(sys.argv[1]);print('Console screenshot saved')
    finally:c.sock.close()
