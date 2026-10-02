"""Scoped build branch publication via GitHub API when Git transport is unavailable."""
import base64,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REPO='yardfribley-bit/agentpair';BRANCH='codex/applens-context-v2'
FILES=['windows/AgentPairWindows.cs','windows/build.ps1','windows/setup.iss','windows/workbuddy-context.ps1','windows/workbuddy-network.ps1','windows/software-install.ps1','windows/prepare-capture.ps1','windows/test-capture-runtime.py','windows/test-context.ps1','windows/COMPLETE_CONTEXT.md','macos/workbuddy_network_capture.py','agentpair/web_assets/agentpair-windows.ps1','.github/workflows/applens-context-windows.yml']
def api(path,payload=None):
    command=['gh','api',path]
    if payload is not None:command+=['--input','-']
    result=subprocess.run(command,input=json.dumps(payload) if payload is not None else None,text=True,capture_output=True,timeout=45)
    if result.returncode:raise RuntimeError('GitHub API failed: '+result.stderr[:300])
    return json.loads(result.stdout)
def main():
    base=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    try:head=api('repos/'+REPO+'/git/ref/heads/'+BRANCH)['object']['sha']
    except RuntimeError:
        head=api('repos/'+REPO+'/git/refs',{'ref':'refs/heads/'+BRANCH,'sha':base})['object']['sha']
    mutation='mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid url}}}'
    data={'branch':{'repositoryNameWithOwner':REPO,'branchName':BRANCH},'expectedHeadOid':head,
          'message':{'headline':'Build prototype-aligned Windows AppLens model-context collector'},
          'fileChanges':{'additions':[{'path':p,'contents':base64.b64encode((ROOT/p).read_bytes()).decode()} for p in FILES]}}
    result=api('graphql',{'query':mutation,'variables':{'input':data}})
    if result.get('errors'):raise RuntimeError(str(result['errors']))
    print(json.dumps(result['data']['createCommitOnBranch']['commit']))
if __name__=='__main__':main()
