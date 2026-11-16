"""Bounded, journalled file capabilities. No transaction claim for shell effects.

Requires exclusive workspace ownership by this task. dirfds/no-follow reject link
escapes; this is not isolation from another malicious process with the same UID.
Content and ordinary modes are restored, not timestamps/ACLs/xattrs/hardlinks.
"""
import base64,contextlib,errno,fcntl,hashlib,json,os,stat,time,uuid
from dataclasses import dataclass,field
from pathlib import Path

LIMIT=1024*1024

def path_name(value):
    if not isinstance(value,str) or not value or '\x00' in value or value.startswith('/'):
        raise ValueError('relative file path required')
    parts=value.split('/')
    if any(p in {'','..','.'} or p.startswith('.neuro-tmp-') for p in parts):raise ValueError('noncanonical or escaping path')
    return value

def digest(data):return hashlib.sha256(data).hexdigest()

@dataclass(frozen=True)
class FilePolicy:
    # Trusted caller supplies capabilities. A model-provided hash grants nothing.
    create:tuple=()
    edit:dict=field(default_factory=dict)
    remove:tuple=()
    def __post_init__(self):
        object.__setattr__(self,'create',tuple(path_name(p) for p in self.create))
        object.__setattr__(self,'remove',tuple(path_name(p) for p in self.remove))
        object.__setattr__(self,'edit',dict(self.edit))
        for p,h in self.edit.items():
            path_name(p)
            if not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h):raise ValueError('edit requires original SHA256')
        if set(self.create)&set(self.edit):raise ValueError('ambiguous create/edit capability')
    def describe(self):return {'create':list(self.create),'edit':list(self.edit),'remove':list(self.remove)}

class SafeFiles:
    def __init__(self,workspace,journal,policy):
        self.work=Path(workspace).resolve();self.journal=Path(journal).resolve();self.policy=FilePolicy(policy.create,policy.edit,policy.remove)
        if self.journal.is_relative_to(self.work):raise ValueError('journal must be outside worker-readable workspace')
        self.journal.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.journal.stat().st_uid!=os.getuid() or self.journal.stat().st_mode&0o077:raise ValueError('private journal required')
        self.expected={};self._rebuild()
    @contextlib.contextmanager
    def lock(self):
        fd=os.open(self.journal/'lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX);yield
        finally:os.close(fd)
    @contextlib.contextmanager
    def parent(self,name):
        parts=path_name(name).split('/');fd=os.open(self.work,os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                try:nxt=os.open(part,os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                except OSError as exc:
                    if exc.errno in {errno.ELOOP,errno.ENOTDIR,errno.ENOENT}:raise ValueError('unsafe or unavailable parent: '+name) from exc
                    raise
                os.close(fd);fd=nxt
            yield fd,parts[-1]
        finally:os.close(fd)
    def snapshot(self,name):
        with self.parent(name) as (fd,leaf):
            try:f=os.open(leaf,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            except FileNotFoundError:return None
            except OSError as exc:raise ValueError('unsafe file: '+name) from exc
            try:
                st=os.fstat(f)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_mode&0o7000 or st.st_size>LIMIT:raise ValueError('bounded regular single-link file required')
                data=b''
                while len(data)<=LIMIT:
                    block=os.read(f,min(65536,LIMIT+1-len(data)))
                    if not block:break
                    data+=block
                after=os.fstat(f)
                if len(data)>LIMIT or (st.st_ino,st.st_size,st.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('file changed during read')
                return {'data':base64.b64encode(data).decode(),'sha256':digest(data),'mode':stat.S_IMODE(st.st_mode)&0o777}
            finally:os.close(f)
    def _publish(self,name,value,create=False,temporary=None):
        with self.parent(name) as (fd,leaf):
            if value is None:
                try:os.unlink(leaf,dir_fd=fd)
                except FileNotFoundError:pass
            else:
                temp=temporary or '.neuro-tmp-'+uuid.uuid4().hex
                f=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:
                    raw=base64.b64decode(value['data']);offset=0
                    while offset<len(raw):offset+=os.write(f,raw[offset:])
                    os.fchmod(f,value['mode']);os.fsync(f)
                finally:os.close(f)
                try:
                    if create:
                        # Atomic no-clobber publication; even an unexpected late
                        # collision cannot replace an existing directory entry.
                        os.link(temp,leaf,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                    else:os.replace(temp,leaf,src_dir_fd=fd,dst_dir_fd=fd)
                finally:
                    try:os.unlink(temp,dir_fd=fd)
                    except FileNotFoundError:pass
            os.fsync(fd)
    def _save(self,record):
        record['updated']=time.time_ns()
        target=self.journal/(record['id']+'.json');temp=self.journal/(record['id']+'.tmp-'+uuid.uuid4().hex)
        with temp.open('x') as f:json.dump(record,f,sort_keys=True);f.flush();os.fsync(f.fileno())
        os.replace(temp,target)
        fd=os.open(self.journal,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    def _records(self):return sorted((json.loads(p.read_text()) for p in self.journal.glob('*.json')),key=lambda r:r['sequence'])
    def _rebuild(self):
        self.expected={}
        for r in sorted(self._records(),key=lambda r:r.get('updated',r['sequence'])):
            if r['state']=='committed':self.expected.update(r['after'])
            elif r['state']=='rolled_back':self.expected.update(r['before'])
    def _authorize(self,path,before,remove=False):
        if remove:
            if path not in self.policy.remove:raise ValueError('source removal not authorized')
            if before is None:raise ValueError('source absent')
        elif before is None:
            if path not in self.policy.create and path not in self.expected:raise ValueError('creation not authorized or original edit target missing')
        elif path not in self.expected:
            if self.policy.edit.get(path)!=before['sha256']:raise ValueError('existing destination collision or stale edit grant')
        if path in self.expected and before!=self.expected[path]:raise ValueError('file changed outside managed operations')
    def apply(self,operation,path,content=None,source=None,fault=None):
        path=path_name(path)
        if operation not in {'write','copy','move'}:raise ValueError('unknown safe operation')
        with self.lock():
            if any(r['state'] in {'prepared','applied','rolling_back','conflict'} for r in self._records()):raise ValueError('unreconciled transaction; recover first')
            self._rebuild();before={path:self.snapshot(path)}
            self._authorize(path,before[path])
            if operation=='write':
                if not isinstance(content,str) or len(content.encode())>LIMIT:raise ValueError('bounded text required')
                value={'data':base64.b64encode(content.encode()).decode(),'sha256':digest(content.encode()),'mode':before[path]['mode'] if before[path] else 0o600}
            else:
                source=path_name(source)
                if source==path:raise ValueError('source equals destination')
                original=self.snapshot(source)
                if original is None:raise ValueError('source missing')
                value=original
                if operation=='move':self._authorize(source,original,remove=True);before[source]=original
            after={path:value}
            if operation=='move':after[source]=None
            record={'id':uuid.uuid4().hex,'sequence':len(self._records()),'state':'prepared','operation':operation,'before':before,'after':after,'temporary':{p:'.neuro-tmp-'+uuid.uuid4().hex for p in before}}
            self._save(record)
            try:
                # Recheck just before applying under the exclusive task lock.
                if any(self.snapshot(p)!=v for p,v in before.items()):raise ValueError('precondition changed')
                self._publish(path,value,create=before[path] is None,temporary=record['temporary'][path])
                if fault=='crash_after_destination':raise SystemExit('controlled crash')
                if fault=='after_destination':raise RuntimeError('controlled fault')
                if operation=='move':self._publish(source,None)
                record['state']='applied';self._save(record)
                if any(self.snapshot(p)!=v for p,v in after.items()):raise RuntimeError('post-write verification failed')
                record['state']='committed';self._save(record);self._rebuild()
                return {'transaction':record['id'],'verified':True,'operation':operation,'paths':{p:v['sha256'] if v else None for p,v in after.items()}}
            except Exception:
                self._rollback(record);raise
    def _cleanup(self,record):
        for path,temp in record.get('temporary',{}).items():
            with self.parent(path) as (fd,_):
                try:os.unlink(temp,dir_fd=fd);os.fsync(fd)
                except FileNotFoundError:pass
    def _rollback(self,record):
        self._cleanup(record)
        for p in record['before']:
            current=self.snapshot(p)
            if current not in [record['before'][p],record['after'][p]]:
                record['state']='conflict';self._save(record);raise ValueError('rollback conflict: '+p)
        record['state']='rolling_back';self._save(record)
        for p,value in record['before'].items():
            if self.snapshot(p)!=value:self._publish(p,value,create=self.snapshot(p) is None,temporary=record['temporary'][p])
        if any(self.snapshot(p)!=v for p,v in record['before'].items()):raise RuntimeError('rollback verification failed')
        record['state']='rolled_back';self._save(record);self._rebuild()
        return {'transaction':record['id'],'verified':True,'restored':list(record['before'])}
    def rollback(self,transaction):
        if not isinstance(transaction,str) or len(transaction)!=32 or any(c not in '0123456789abcdef' for c in transaction):raise ValueError('invalid transaction ID')
        with self.lock():return self._rollback(json.loads((self.journal/(transaction+'.json')).read_text()))
    def recover(self):
        with self.lock():return [self._rollback(r) for r in reversed(self._records()) if r['state'] in {'prepared','applied','rolling_back','conflict'}]
