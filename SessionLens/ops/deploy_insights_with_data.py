"""Deploy session insight UI through the existing AgentPair reverse proxy."""
import getpass,subprocess,os,json,shlex,io,tarfile
from pathlib import Path
password=getpass.getpass('SSH password: ')
def ssh(command,data=b''):
 r,w=os.pipe();os.write(w,(password+'\n').encode());os.close(w)
 try:
  p=subprocess.run(['sshpass','-d',str(r),'ssh','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/private/tmp/vmess-50.118.187.180-known_hosts','-o','ConnectTimeout=10','root@50.118.187.180',command],pass_fds=(r,),input=data,capture_output=True,timeout=120)
 finally:os.close(r)
 if p.returncode:raise RuntimeError(p.stderr.decode()[-2500:])
 return p.stdout
if __name__=='__main__':
 print(ssh("systemctl cat agentpair-certbot.service; ls /opt; ls /root/.acme.sh/acme.sh 2>/dev/null; python3 -m pip --version").decode())
