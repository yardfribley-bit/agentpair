"""Local CPU embedding with immutable model identity and no network code."""
import hashlib,json,os,threading
from pathlib import Path

_cache={};_cache_lock=threading.Lock()

class LocalEmbedding:
    def __init__(self,config):
        self.directory=Path(config['directory']).expanduser().resolve()
        manifest=json.loads((self.directory/'manifest.json').read_text())
        if manifest.get('pooling')!='cls' or manifest.get('dimensions')!=512:raise ValueError('本地 embedding 模型清单不兼容')
        self.max_tokens=max(32,min(512,int(config.get('maxTokens',256))))
        self.threads=max(1,min(2,int(config.get('threads',1))))
        for name in ('model.onnx','tokenizer.json','config.json'):
            if hashlib.sha256((self.directory/name).read_bytes()).hexdigest()!=manifest['files'].get(name):raise ValueError('embedding 模型文件校验失败：'+name)
        self.name=manifest['name'];self.dimensions=manifest['dimensions'];self.instruction=manifest['queryInstruction']
        self.identity=hashlib.sha256(json.dumps({'files':manifest['files'],'pooling':'cls','dimensions':self.dimensions,'maxTokens':self.max_tokens,'instruction':self.instruction,'normalization':'l2'},sort_keys=True).encode()).hexdigest()
        os.environ.setdefault('RAYON_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('VECLIB_MAXIMUM_THREADS','1')
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        ort.disable_telemetry_events();options=ort.SessionOptions();options.intra_op_num_threads=self.threads;options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL;options.enable_cpu_mem_arena=False;options.log_severity_level=3
        self.session=ort.InferenceSession(str(self.directory/'model.onnx'),options,providers=['CPUExecutionProvider'])
        self.tokenizer=Tokenizer.from_file(str(self.directory/'tokenizer.json'));self.tokenizer.enable_truncation(max_length=self.max_tokens)
        self.tokenizer.enable_padding(pad_id=0,pad_token='[PAD]');self.lock=threading.Lock();self.np=np
    def encode(self,texts,query=False):
        if not isinstance(texts,list) or not 1<=len(texts)<=16 or any(not isinstance(t,str) for t in texts):raise ValueError('embedding 输入必须是 1 至 16 段文本')
        np=self.np
        with self.lock:
            encoded=self.tokenizer.encode_batch([(self.instruction if query else '')+t[:4000] for t in texts])
            feeds={'input_ids':np.asarray([e.ids for e in encoded],dtype=np.int64),
                   'attention_mask':np.asarray([e.attention_mask for e in encoded],dtype=np.int64),
                   'token_type_ids':np.asarray([e.type_ids for e in encoded],dtype=np.int64)}
            feeds={item.name:feeds[item.name] for item in self.session.get_inputs()}
            hidden=self.session.run(None,feeds)[0]
            values=hidden[:,0,:].astype(np.float32) if hidden.ndim==3 else hidden.astype(np.float32)
            if values.shape!=(len(texts),self.dimensions) or not np.isfinite(values).all():raise ValueError('embedding 输出维度或内容无效')
            norms=np.linalg.norm(values,axis=1,keepdims=True)
            if not (norms>0).all():raise ValueError('embedding 输出为空向量')
            return (values/norms).tolist()
    def query(self,text):return self.encode([text],query=True)[0]

def load(config):
    if not config.get('enabled'):return None
    if config.get('provider','local-onnx')!='local-onnx':raise ValueError('当前仅支持本地 ONNX embedding')
    path=Path(config['directory']).expanduser().resolve()
    manifest=path/'manifest.json';model=path/'model.onnx';tokenizer=path/'tokenizer.json'
    key=(str(path),manifest.stat().st_mtime_ns,model.stat().st_mtime_ns,model.stat().st_size,tokenizer.stat().st_mtime_ns,config.get('maxTokens',256),config.get('threads',1))
    with _cache_lock:
        if key not in _cache:
            engine=LocalEmbedding(config);_cache.clear();_cache[key]=engine
        return _cache[key]
