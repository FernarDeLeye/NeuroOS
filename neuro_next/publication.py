"""Capability-controlled publication of bounded binary/text files and directories.

Commands never receive a live directory fd. Publication is a separate trusted
operation. Multi-file updates are journalled, not atomically visible as a group.
Requires exclusive task ownership; conflicts stop rollback instead of clobbering.
"""
import base64,contextlib,fcntl,json,os,time,uuid
from dataclasses import dataclass,field
from pathlib import Path
from .workspace_tree import logical_path,parent_fd,read_entry,snapshot,MAX_TOTAL

@dataclass(frozen=True)
class PublicationPolicy:
    create:tuple=()
    create_roots:tuple=()
    edit:dict=field(default_factory=dict)
    remove:tuple=()
    def __post_init__(self):
        for key in ['create','create_roots','remove']:object.__setattr__(self,key,tuple('.' if key=='create_roots' and p=='.' else logical_path(p) for p in getattr(self,key)))
        object.__setattr__(self,'edit',{logical_path(p):h for p,h in self.edit.items()})
        for h in self.edit.values():
            if not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h):raise ValueError('edit grant requires original SHA256')
    def can_create(self,p):return '.' in self.create_roots or p in self.create or any(p==r or p.startswith(r+'/') for r in self.create_roots)
    def describe(self):return {'create':list(self.create),'create_roots':list(self.create_roots),'edit':list(self.edit),'remove':list(self.remove)}

class Publisher:
    def __init__(self,live,journal,policy):
        self.live=Path(live).resolve();self.journal=Path(journal).resolve();self.policy=PublicationPolicy(policy.create,policy.create_roots,policy.edit,policy.remove)
        if self.journal.is_relative_to(self.live):raise ValueError('journal must be outside publication tree')
        self.journal.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.journal.stat().st_uid!=os.getuid() or self.journal.stat().st_mode&0o077:raise ValueError('private journal required')
    @contextlib.contextmanager
    def lock(self):
        f=os.open(self.journal/'lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(f,fcntl.LOCK_EX);yield
        finally:os.close(f)
    def records(self):return sorted((json.loads(p.read_text()) for p in self.journal.glob('*.json')),key=lambda r:r['updated'])
    def known(self):
        result={}
        for r in self.records():
            if r['state']=='committed':result.update({p:v for p,v in r['after'].items() if r['before'][p]!=v})
            elif r['state']=='rolled_back':result.update({p:v for p,v in r['before'].items() if r['after'][p]!=v})
        return result
    def _identity(self,p):
        try:fd,name=parent_fd(self.live,p)
        except FileNotFoundError:return None
        try:
            try:st=os.stat(name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:return None
            import stat
            common=[st.st_dev,st.st_ino,st.st_uid,st.st_gid,st.st_mode]
            return common if stat.S_ISDIR(st.st_mode) else common+[st.st_mtime_ns,st.st_ctime_ns]
        finally:os.close(fd)
    def known_identities(self):
        result={}
        for r in self.records():
            if r['state'] in {'committed','rolled_back'}:result.update(r.get('identities',{}))
        return result
    def _get(self,p):
        try:fd,name=parent_fd(self.live,p)
        except FileNotFoundError:return None
        except OSError as exc:raise ValueError('unsafe live ancestor') from exc
        try:
            try:value=read_entry(fd,name)
            except FileNotFoundError:return None
            if value['kind']=='symlink':raise ValueError('live symlink not publishable')
            return value
        finally:os.close(fd)
    def _save(self,record):
        record['updated']=time.time_ns();temp=self.journal/(uuid.uuid4().hex+'.tmp')
        with temp.open('x') as f:json.dump(record,f);f.flush();os.fsync(f.fileno())
        os.replace(temp,self.journal/(record['id']+'.json'))
        fd=os.open(self.journal,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    def _set(self,p,value,current,temp):
        fd,name=parent_fd(self.live,p)
        try:
            if value is None:
                if current and current['kind']=='dir':os.rmdir(name,dir_fd=fd)
                elif current:os.unlink(name,dir_fd=fd)
            elif value['kind']=='dir':
                if current is None:os.mkdir(name,0o700,dir_fd=fd)
                elif current['kind']!='dir':raise ValueError('directory/file collision')
            else:
                f=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:
                    data=base64.b64decode(value['data']);offset=0
                    while offset<len(data):offset+=os.write(f,data[offset:])
                    os.fchmod(f,value['mode']);os.fsync(f)
                finally:os.close(f)
                try:
                    if current is None:os.link(temp,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                    else:os.replace(temp,name,src_dir_fd=fd,dst_dir_fd=fd)
                finally:
                    try:os.unlink(temp,dir_fd=fd)
                    except FileNotFoundError:pass
            os.fsync(fd)
        finally:os.close(fd)
    def _cleanup(self,r):
        for p,temp in r['temporary'].items():
            try:fd,_=parent_fd(self.live,p)
            except FileNotFoundError:continue
            try:
                try:os.unlink(temp,dir_fd=fd);os.fsync(fd)
                except FileNotFoundError:pass
            finally:os.close(fd)
    def _authorize(self,p,before,after,known):
        if before==after:return
        if p in known:
            if before!=known[p]:raise ValueError('external modification: '+p)
            # Owning a created output does not grant removal of an input.
            if after is None and p not in self.policy.remove:raise ValueError('removal not authorized: '+p)
            if after is not None and not self.policy.can_create(p) and p not in self.policy.edit:raise ValueError('creation/edit not authorized: '+p)
            return
        if after is None:
            if p not in self.policy.remove:raise ValueError('removal not authorized: '+p)
        elif before is None:
            if not self.policy.can_create(p):raise ValueError('creation not authorized: '+p)
        elif before['kind']=='dir' and after['kind']=='dir':return
        elif before['kind']!='file' or self.policy.edit.get(p)!=before.get('sha256'):raise ValueError('collision or stale edit grant: '+p)
    def eligible(self,scratch):
        source=snapshot(scratch);known=self.known()
        return sorted(p for p in source.keys()|set(self.policy.remove) if p in known or self.policy.can_create(p) or p in self.policy.edit or p in self.policy.remove)
    def publish(self,scratch,paths,fault=None):
        if not isinstance(paths,list) or not 1<=len(paths)<=32 or len(set(paths))!=len(paths):raise ValueError('bounded distinct publication paths required')
        selected=[logical_path(p) for p in paths]
        with self.lock():
            if any(r['state'] not in {'committed','rolled_back'} for r in self.records()):raise ValueError('unreconciled publication journal')
            source=snapshot(scratch);after={};known=self.known();known_ids=self.known_identities()
            for path in selected:
                value=source.get(path)
                if value and value['kind']=='dir':
                    for p,v in source.items():
                        if p==path or p.startswith(path+'/'):after[p]=dict(v)
                else:after[path]=dict(value) if value else None
            # New parents require explicit directory-root capabilities too.
            for p in list(after):
                parts=p.split('/')
                for i in range(1,len(parts)):
                    parent='/'.join(parts[:i])
                    if self._get(parent) is None:
                        if source.get(parent,{}).get('kind')!='dir':raise ValueError('missing source parent')
                        after[parent]={'kind':'dir','mode':0o700}
            if len(after)>256 or sum(v.get('size',0) for v in after.values() if v)>16*1024**2:raise ValueError('publication batch bounds exceeded')
            before={p:self._get(p) for p in after}
            for p,v in after.items():
                if hasattr(self,'initial_snapshot') and p not in known and before[p]!=self.initial_snapshot.get(p):raise ValueError('live data differs from registered baseline: '+p)
                if p in known_ids and self._identity(p)!=known_ids[p]:raise ValueError('external identity/metadata modification: '+p)
                if v and v['kind']=='symlink':raise ValueError('symlinks cannot be published')
                if v and v['kind']=='file':v['mode']=before[p]['mode'] if before[p] and before[p]['kind']=='file' else 0o600
                if v and v['kind']=='dir':v['mode']=before[p]['mode'] if before[p] else 0o700
                self._authorize(p,before[p],v,known)
            after={p:v for p,v in after.items() if v!=before[p]};before={p:before[p] for p in after}
            record={'id':uuid.uuid4().hex,'state':'prepared','before':before,'after':after,
                    'temporary':{p:'.neuro-control-'+uuid.uuid4().hex for p in after},'identities':{p:self._identity(p) for p in after}}
            if getattr(self,'operation_id',None) is not None:record['operation_id']=self.operation_id
            self._save(record)
            try:
                if any(self._get(p)!=v for p,v in before.items()):raise ValueError('live precondition changed')
                # Source was captured with no-follow, nlink/size/inode checks.
                # Recheck it after planning. No command is running at publish.
                if snapshot(scratch)!=source:raise ValueError('scratch changed during publication')
                files=0
                for p in sorted(after,key=lambda p:(after[p] is None,p.count('/') if after[p] else -p.count('/'),p)):
                    if self._get(p)!=before[p]:raise ValueError('publication race/conflict')
                    if before[p]!=after[p]:
                        self._set(p,after[p],before[p],record['temporary'][p])
                        record['identities'][p]=self._identity(p);self._save(record)
                        if getattr(self,'checkpoint',None):self.checkpoint('publication_file_committed',record)
                    if after[p] and after[p]['kind']=='file':
                        files+=1
                        if files==1 and fault=='after_first_file':raise RuntimeError('controlled publication fault')
                        if files==1 and fault=='crash_after_first_file':raise SystemExit('controlled publication crash')
                if any(self._get(p)!=v for p,v in after.items()):raise RuntimeError('published bytes verification failed')
                record['state']='committed';self._save(record)
                return {'transaction':record['id'],'verified':True,'published':{p:{k:v[k] for k in ['kind','sha256'] if k in v} if v else None for p,v in after.items()}}
            except Exception:self._rollback(record);raise
    def _rollback(self,r):
        self._cleanup(r)
        for p in r['before']:
            if self._get(p) not in [r['before'][p],r['after'][p]] or self._identity(p)!=r['identities'][p]:
                r['state']='conflict';self._save(r);raise ValueError('rollback conflict: '+p)
        r['state']='rolling_back';self._save(r)
        desired=r['before']
        for p in sorted(desired,key=lambda p:(desired[p] is None,p.count('/') if desired[p] else -p.count('/'),p)):
            current=self._get(p)
            if current not in [r['before'][p],r['after'][p]] or self._identity(p)!=r['identities'][p]:raise ValueError('rollback concurrent conflict')
            if current!=desired[p]:
                self._set(p,desired[p],current,r['temporary'][p]);r['identities'][p]=self._identity(p);self._save(r)
        if any(self._get(p)!=v for p,v in desired.items()):raise RuntimeError('rollback did not restore recorded state')
        r['state']='rolled_back';self._save(r)
        return {'transaction':r['id'],'verified':True,'restored':list(desired)}
    def rollback(self,transaction):
        if not isinstance(transaction,str) or len(transaction)!=32 or any(c not in '0123456789abcdef' for c in transaction):raise ValueError('invalid transaction')
        with self.lock():return self._rollback(json.loads((self.journal/(transaction+'.json')).read_text()))
    def recover(self):
        with self.lock():return [self._rollback(r) for r in reversed(self.records()) if r['state'] not in {'committed','rolled_back'}]
