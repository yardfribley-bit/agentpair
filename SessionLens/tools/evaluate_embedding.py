"""Offline, reproducible task retrieval evaluation; no relay/model API calls."""
import argparse,hashlib,json,os,sqlite3,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sessionlens.embedding import load
from sessionlens.embedding_store import EmbeddingStore,goal_text
from sessionlens.knowledge_index import KnowledgeIndex,fingerprint
from sessionlens.knowledge import retrieve_candidates,select_task
from sessionlens.task_lineage import resolve

def run(collector,config,dataset,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);os.chmod(folder,0o700)
    source=sqlite3.connect(Path(collector).resolve().as_uri()+'?mode=ro',uri=True)
    engine=load(config);vectors=EmbeddingStore(folder/'embeddings.db');lexical=KnowledgeIndex(folder/'knowledge.db')
    rows=source.execute('SELECT id,source,session,prompt,updated,state,last_row,turn_count,requirements FROM task_groups ORDER BY id').fetchall()
    corpus_hash=hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()
    # Identical task-goal corpus for both arms. This evaluation does not compare
    # an event-rich BM25 index with a goal-only semantic index.
    with lexical.db:
        lexical.db.execute('DELETE FROM kb_tasks')
        for row in rows:
            if row[3].startswith('The following is the Codex agent history'):continue
            lexical.db.execute('INSERT INTO kb_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',(*row[:6],row[6],row[7],fingerprint(row),0,1))
            lexical._chunk('task:'+row[0],row[0],row[0],row[0],'任务目标',0,goal_text(row[3],row[8]))
    started=time.monotonic();vectors.sync(source,engine,limit=16,force=True);batches=0
    while vectors.pending:
        vectors.sync(source,engine,limit=16);batches+=1
        if batches%50==0:print('embedded',vectors.status()['readyTasks'],flush=True)
        if vectors.paused:raise ValueError('评测向量索引达到预算，不能把不完整语料当作完整验收')
    build_seconds=time.monotonic()-started;results=[]
    for case in dataset:
        gold={resolve(source,i) for i in case.get('taskIds',[])};arms={}
        for arm in ('bm25','hybrid'):
            plan=case.get('plan',{'terms':[],'subjects':[],'followup':False});started=time.monotonic()
            found=retrieve_candidates(source,plan,case['question'],source=case.get('source'),index=lexical,
                                      embedder=engine if arm=='hybrid' else None,vectors=vectors if arm=='hybrid' else None)
            ids=[r[0] for r in found];selected,mode=select_task(source,plan,case['question'],[],found)
            rank=next((n for n,i in enumerate(ids,1) if i in gold),None)
            arms[arm]={'top1Correct':bool(ids and ids[0] in gold),'hit5':bool(set(ids[:5])&gold),
                       'reciprocalRank':1/rank if rank else 0,'seconds':round(time.monotonic()-started,4),
                       'mode':mode,'automaticCorrect':selected in gold if selected else False,
                       'incorrectAutomatic':bool(selected and selected not in gold),'ranks':ids[:5]}
        results.append({'id':case['id'],'split':case.get('split','holdout'),'question':case['question'],'goldTasks':sorted(gold),'arms':arms})
    summary={}
    for split in ('all','dev','holdout'):
        cases=[r for r in results if (split=='all' or r['split']==split) and r['goldTasks']]
        for arm in ('bm25','hybrid'):
            summary[split+':'+arm]={'questions':len(cases),'top1':round(sum(r['arms'][arm]['top1Correct'] for r in cases)/max(1,len(cases)),4),
                                  'hit5':round(sum(r['arms'][arm]['hit5'] for r in cases)/max(1,len(cases)),4),
                                  'mrr':round(sum(r['arms'][arm]['reciprocalRank'] for r in cases)/max(1,len(cases)),4),
                                  'incorrectAutomatic':sum(r['arms'][arm]['incorrectAutomatic'] for r in cases)}
    report={'scope':'offline task-goal retrieval; not answer accuracy or full-history completeness','corpusTasks':len(rows),'corpusSha256':corpus_hash,
            'model':engine.name,'modelIdentity':engine.identity,'dimensions':engine.dimensions,'datasetSha256':hashlib.sha256(json.dumps(dataset,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'vectorBuildSeconds':round(build_seconds,3),'vectorBytes':vectors.status()['bytes'],'networkRequests':0,'summary':summary,'cases':results}
    path=folder/'results.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2));os.chmod(path,0o600)
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False),flush=True)
    vectors.close();lexical.close();source.close();return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--collector',required=True);parser.add_argument('--config',required=True);parser.add_argument('--dataset',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    run(args.collector,json.loads(Path(args.config).read_text()),json.loads(Path(args.dataset).read_text()),args.output)
