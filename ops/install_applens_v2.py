"""Install the built AppLens with a recoverable backup, bootstrap only if needed."""
import datetime,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from deploy_persistent_navigator import ssh

def main():
    app=Path('/Applications/AppLens.app')
    subprocess.run(['pkill','-x','AppLens'],check=False)
    if app.exists():
        backup=Path('/Applications/AppLens-backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.app')
        subprocess.run(['ditto',str(app),str(backup)],check=True)
    subprocess.run(['ditto',str(ROOT/'macos/build/AppLens.app'),str(app)],check=True)
    # Existing installations before V2 held tokens only in memory; V2 persists in Keychain.
    existing=subprocess.run(['security','find-generic-password','-s','AppLensDevice','-a','https://50.118.187.180'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    args=['open',str(app)]
    if not existing:
        code=ssh("cd /opt/agentpair && python3 -c \"from agentpair.devices import DeviceStore;print(DeviceStore('/var/lib/agentpair/devices.db').pairing()['code'])\"").decode().strip()
        args+=['--args','--pair-code',code]
    subprocess.run(args,check=True)
    print('Installed and launched AppLens V2; previous app retained as backup. No credentials printed.')
if __name__=='__main__':main()
