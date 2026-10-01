"""Build a dependency-free test APK using installed Android SDK tools and JDK."""
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

root=Path(__file__).resolve().parent
sdk=Path(os.environ.get('ANDROID_HOME',str(Path.home()/'Library/Android/sdk')))
tools=sdk/'build-tools/36.0.0';jar=sdk/'platforms/android-36/android.jar'
out=root/'build';out.mkdir(exist_ok=True)
classes=out/'classes';classes.mkdir(exist_ok=True)
dex=out/'dex';dex.mkdir(exist_ok=True)
def run(args):subprocess.run([str(x) for x in args],check=True)
run(['javac','-source','8','-target','8','-encoding','UTF-8','-classpath',jar,'-d',classes,*sorted((root/'src').rglob('*.java'))])
run([tools/'d8','--lib',jar,'--min-api','26','--output',dex,*sorted(classes.rglob('*.class'))])
apk=out/'unsigned.apk'
resources=out/'resources.zip'
run([tools/'aapt2','compile','--dir',root/'res','-o',resources])
run([tools/'aapt2','link','-I',jar,'--manifest',root/'AndroidManifest.xml','-o',apk,resources])
with zipfile.ZipFile(apk,'a') as archive:
    for file in dex.glob('*.dex'):archive.write(file,file.name)
aligned=out/'aligned.apk';run([tools/'zipalign','-f','4',apk,aligned])
key=out/'debug.keystore'
if not key.exists():run(['keytool','-genkeypair','-keystore',key,'-storepass','android','-alias','androiddebugkey','-keypass','android','-dname','CN=AgentPair Development','-keyalg','RSA','-keysize','2048','-validity','3650'])
final=out/'AgentPair-Android-0.1.0.apk'
run([tools/'apksigner','sign','--ks',key,'--ks-pass','pass:android','--out',final,aligned])
run([tools/'apksigner','verify',final])
shutil.copy2(final,root.parent/'agentpair'/'web_assets'/'AgentPair-Android-0.1.0.apk')
print(final)
