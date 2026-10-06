"""Explicit model installation. Runtime never downloads models or uploads text."""
import argparse,hashlib,json,os,urllib.request
from pathlib import Path

REPOSITORY='Xenova/bge-small-zh-v1.5'
REVISION='75c43b069aac4d136ba6bc1122f995fedcfd2781'
FILES={'model.onnx':'onnx/model_quantized.onnx','tokenizer.json':'tokenizer.json','config.json':'config.json'}

def install(folder):
    folder=Path(folder).expanduser().resolve();folder.mkdir(parents=True,exist_ok=True);hashes={}
    for local,remote in FILES.items():
        target=folder/local;temporary=folder/(local+'.download')
        url=f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{remote}'
        with urllib.request.urlopen(url,timeout=60) as response,temporary.open('wb') as output:
            while block:=response.read(1024*1024):output.write(block)
        temporary.replace(target);os.chmod(target,0o600);hashes[local]=hashlib.sha256(target.read_bytes()).hexdigest()
        print(local,target.stat().st_size,'bytes',flush=True)
    manifest={'name':'bge-small-zh-v1.5-int8','repository':REPOSITORY,'revision':REVISION,'files':hashes,
              'dimensions':512,'pooling':'cls','queryInstruction':'为这个句子生成表示以用于检索相关文章：','license':'MIT'}
    path=folder/'manifest.json';path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(path,0o600)
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',required=True);args=parser.parse_args();install(args.directory)
