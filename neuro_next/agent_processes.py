"""Agent facade over existing persistent.exec workers; not a second supervisor.

Uses the qualified persistent workers, sandbox, PTY and journals without a new supervisor.
"""
import hashlib,json,os,time,uuid
from pathlib import Path
from neuro_next.durable import save
from neuro_next.execution_workspace import ExecutionWorkspace,remove_private_tree
from neuro_next.operation_journal import digest,UncertainOperation
from neuro_next.workspace_tree import snapshot,materialize,metadata,logical_path

class AgentProcesses:
    def __init__(self,store,conversation,parent):
        self.store,self.conversation,self.parent=store,conversation,parent
        store.db.execute('CREATE TABLE IF NOT EXISTS conversation_processes(conversation TEXT NOT NULL,task TEXT PRIMARY KEY,parent TEXT NOT NULL,intent INTEGER NOT NULL,UNIQUE(parent,intent))');store.db.commit()
    def ids(self):return [r['task'] for r in self.store.db.execute('SELECT task FROM conversation_processes WHERE conversation=? ORDER BY rowid',(self.conversation,))]
    def owned(self,task):
        if task not in self.ids():raise ValueError('process outside this conversation')
        return task
    def start(self,action,workspace,intent):
        from neuro_next.persistent import submit,launch,unit_state
        from neuro_next.reliability import strict_action
        row=self.store.db.execute('SELECT task FROM conversation_processes WHERE parent=? AND intent=?',(self.parent,intent)).fetchone()
        if row:return self.poll(row['task']) # Never launch an already registered operation.
        if sum(unit_state(t).get('ActiveState') in {'active','activating','deactivating'} for t in self.ids())>=2:raise ValueError('two live processes already active; poll or stop one first')
        shell={'kind':'shell',**{k:v for k,v in action.items() if k in {'command','cwd','stdin','env'}}}
        strict_action(shell|{'cwd':logical_path(shell.get('cwd','.'),root=True) or '.'})
        if shell.get('cwd','.') not in workspace.cwds():raise ValueError('process cwd is not advertised')
        seconds=action.get('seconds',300)
        task=submit(self.store,'Изолированный процесс для задачи '+self.parent,input_dir=workspace.scratch,options={'process_action':shell,'seconds':max(30,seconds),'shell_seconds':seconds,'steps':1,'pty':action.get('pty',False),'open_stdin':not action.get('pty',False),'owner_parent':self.parent})
        with self.store.db:self.store.db.execute('INSERT INTO conversation_processes VALUES(?,?,?,?)',(self.conversation,task,self.parent,intent))
        launch(self.store,task)
        # Reuse supervision's actual output. Do not spend another LLM turn just
        # to obtain a prompt that is already available, or wait for job exit.
        end=time.monotonic()+1
        while True:
            result=self.poll(task);data=result['process_state']
            if any(data['stream_bytes'].values()) or data['exit_status_known'] or time.monotonic()>=end:break
            time.sleep(.05)
        return result|{'initial_observation_window_s':1,'launch_requested':True}
    def poll(self,task):
        from neuro_next.persistent import status,unit_state
        self.owned(task);s=status(self.store,task);u=unit_state(task);root=self.store.root/(task+'.execution')
        events=self.store.events(task);intent=next((e for e in reversed(events) if e['kind']=='intent'),None)
        observation_event=next((e for e in reversed(events) if e['kind']=='observation'),None)
        observation=observation_event['data'] if observation_event else None
        stream={};lengths={}
        if intent:
            for name in ['stdout','stderr']:
                p=root/('operation-'+str(intent['id'])+'.'+name);raw=p.read_bytes() if p.exists() else b'';stream[name]=raw[-4096:].decode(errors='replace');lengths[name]=len(raw)
        live=u.get('ActiveState') in {'active','activating','deactivating'}
        phase='RUNNING' if live else 'EXITED' if observation and s['status']!='uncertain' else 'UNCERTAIN'
        data={'process_id':task,'state':phase,'controller_status':s['status'],'stdout':stream.get('stdout',''),'stderr':stream.get('stderr',''),'stream_bytes':lengths,'exit_code':observation.get('exit_code') if observation else None,'exit_status_known':bool(observation and observation.get('exit_code')is not None),'pty':self.store.specification(task)['options'].get('pty',False),'stdin_mode':'pty' if self.store.specification(task)['options'].get('pty',False) else 'pipe','partial_output_is_not_completion_proof':True,'source_task':task,'source_intent':intent['id'] if intent else None,'source_event':observation_event['id'] if observation_event else None,**{key:observation.get(key) if observation else None for key in ['timed_out','cancelled','output_limited','duration_s','error']}}
        data['configured_timeout_s']=self.store.specification(task)['options'].get('shell_seconds')
        return {'exit_code':0,'stdout':json.dumps(data,ensure_ascii=False),'stderr':'','process_state':data}
    def input(self,task,text=None,eof=False,intent=None):
        from neuro_next.persistent import send_input
        self.owned(task);request_id=self.parent+':'+str(intent) if intent else None
        if request_id and any(e['kind']=='process_input' and e['data'].get('request_id')==request_id for e in self.store.events(task)):return {'exit_code':0,'stdout':json.dumps({'queued':True,'already_recorded':True}),'stderr':''}
        data=send_input(self.store,task,text,eof,request_id=request_id)
        return {'exit_code':0,'stdout':json.dumps(data),'stderr':'','stdin_is_transport_not_task_verification':True}
    def stop(self,task):
        from neuro_next.persistent import request,unit_state,launch
        self.owned(task);request(self.store,task,'cancel')
        if unit_state(task).get('ActiveState') not in {'active','activating'}:launch(self.store,task)
        return {'exit_code':0,'stdout':json.dumps({'process_id':task,'stop_requested':True,'completion_not_yet_confirmed':True}),'stderr':''}
    def progress(self):
        # Real supervised process/IO state, not presentation/timestamps of ls.
        signatures={}
        for task in self.ids():
            events=self.store.events(task);intent=next((e for e in reversed(events) if e['kind']=='intent'),None)
            obs=next((e['data'] for e in reversed(events) if e['kind']=='observation'),None)
            # UUIDs are handles, not useful new work. Repeating the same failed
            # program with the same inputs/output must not reset the guard.
            row={'action':self.store.specification(task)['options']['process_action'],'state':self.store.get(task)['status'],'exit':obs.get('exit_code') if obs else None}
            if obs:row['changes']={p:v for p,v in obs.get('changes',{}).items() if p!='.tmp' and not p.startswith('.tmp/')}
            if intent:
                for name in ['stdout','stderr']:
                    p=self.store.root/(task+'.execution')/('operation-'+str(intent['id'])+'.'+name)
                    row[name]=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
            signatures[json.dumps(row,sort_keys=True)]=row
        return [signatures[k] for k in sorted(signatures)]
    def artifact_owned(self,task):
        if task not in self.ids() and not self.store.db.execute('SELECT 1 FROM task_workspaces WHERE task=? AND conversation=?',(task,self.conversation)).fetchone():raise ValueError('artifact outside this conversation')
        if task==self.parent:raise ValueError('current scratch needs no cross-task import')
        return task
    def source(self,task):
        from neuro_next.persistent import unit_state
        self.artifact_owned(task)
        if unit_state(task).get('ActiveState') in {'active','activating','deactivating'}:raise UncertainOperation('Source controller must stop before artifact collection')
        root=self.store.root/(task+'.execution');source=snapshot(root/'scratch');checkpoint=self.store.runtime(task)['checkpoint']
        trusted=checkpoint.get('workspace_digest')==digest(source) and self.store.get(task)['status']!='uncertain'
        if not trusted:
            receipts=[json.loads(p.read_text()) for p in root.glob('operation-*.json')]
            trusted=any(r.get('phase')=='IMPORTED' and r.get('after_sha256')==digest(source) for r in receipts)
        if not trusted:raise UncertainOperation('Source scratch lacks a matching trusted completed generation')
        baseline=self.store.root/(task+'.baseline.json')
        before=json.loads(baseline.read_text()) if baseline.exists() else snapshot(self.store.workspace(task))
        return source,before
    @staticmethod
    def content(value):
        if value is None:return None
        return {k:value[k] for k in ['kind','sha256','target'] if k in value}
    def candidates(self,live):
        rows=[];target=snapshot(live)
        tasks=self.ids()[-4:]+[r['task'] for r in self.store.db.execute('SELECT task FROM task_workspaces WHERE conversation=? ORDER BY rowid DESC LIMIT 6',(self.conversation,)) if r['task']!=self.parent]
        for task in dict.fromkeys(tasks):
            try:tree,before=self.source(task)
            except (ValueError,OSError,UncertainOperation):continue
            paths=[p for p,v in tree.items() if p!='.tmp' and not p.startswith('.tmp/') and self.content(v)!=self.content(before.get(p)) and self.content(v)!=self.content(target.get(p))]
            if paths:rows.append({'task_id':task,'paths':paths[:12],'available_path_count':len(paths),'kind':'prepared_private_artifacts','published':False,'source_is_not_independent_goal_verification':True})
        return rows[-6:]
    def scope_parent(self,task):
        row=self.store.db.execute('SELECT parent FROM conversation_processes WHERE task=? AND conversation=?',(task,self.conversation)).fetchone()
        return row['parent'] if row else task
    def positive_events(self,task):
        result={}
        for event in self.store.events(task):
            if event['kind']!='observation':continue
            for path,change in event['data'].get('changes',{}).items():
                if change.get('after') is not None:result[path]=event['id']
        return result
    def manifest_sources(self,live,allowed=None):
        groups={}
        for candidate in self.candidates(live):
            task=candidate['task_id']
            if allowed is not None and task not in allowed:continue
            try:
                source,_=self.source(task);root=str(self.store.workspace(self.scope_parent(task)));positive=self.positive_events(task)
            except (OSError,ValueError,UncertainOperation):continue
            for path in candidate['paths']:
                branch={p:v for p,v in source.items() if p==path or p.startswith(path+'/')}
                identity=digest(branch)+':'+root
                groups.setdefault(path,[]).append((identity,positive.get(path,0),task))
        # Identical generations of the same logical object are aliases, not new
        # independent evidence. Different bytes/metadata/scopes remain ambiguous.
        return {path:max(values,key=lambda v:(v[1],v[2]))[2] for path,values in groups.items() if len({v[0] for v in values})==1}

    def transfer(self,task,paths,workspace,intent):
        if not isinstance(paths,list) or not 1<=len(paths)<=32:raise ValueError('bounded import paths required')
        self.artifact_owned(task);journal=workspace.root/('transfer-'+str(intent)+'.json')
        if journal.exists():return self.reconcile_transfer(workspace,intent)
        try:source,before=self.source(task)
        except UncertainOperation as exc:raise ValueError('Source is not ready for import; parent scratch was not changed: '+str(exc)) from exc
        current=workspace.tree();selected=set()
        for path in paths:
            p=logical_path(path)
            selected.add(p);selected.update(v for v in source.keys()|before.keys() if v.startswith(p+'/'))
            pieces=p.split('/')
            for i in range(1,len(pieces)):selected.add('/'.join(pieces[:i]))
        for path in paths:
            p=logical_path(path)
            if before.get(p,{}).get('kind')=='dir' and source.get(p) is None:
                old_branch={q:v for q,v in before.items() if q==p or q.startswith(p+'/')}
                now_branch={q:v for q,v in current.items() if q==p or q.startswith(p+'/')}
                if old_branch!=now_branch:raise ValueError('directory acquired foreign changes; no import: '+p)
        merged=dict(current)
        for p in sorted(selected):
            old,new=before.get(p),source.get(p)
            if self.content(current.get(p))==self.content(new):continue
            if old is None and new and new['kind']=='dir' and current.get(p,{}).get('kind')=='dir':continue
            if old==new:
                if new and new['kind']=='dir' and current.get(p)!=old:raise ValueError('parent directory changed since process launch: '+p)
                continue
            if new and new['kind']=='symlink':raise ValueError('symlink import is forbidden')
            if current.get(p)!=old:raise ValueError('parent scratch changed since process launch: '+p)
            if new is None:merged.pop(p,None)
            else:merged[p]=new
        from .workspace_tree import MAX_TOTAL,MAX_ENTRIES
        if len(merged)>MAX_ENTRIES or sum(v.get('size',0) for v in merged.values())>MAX_TOTAL:raise ValueError('Combined scratch exceeds existing workspace quota; no staging write or exchange')
        temp=workspace.root/('transfer-'+str(intent));temp.mkdir(mode=0o700);materialize(merged,temp)
        for p in temp.rglob('*'):
            if p.is_file():
                with p.open('rb') as f:os.fsync(f.fileno())
        changes={p:{'before':metadata(current).get(p),'after':metadata(merged).get(p)} for p in sorted(selected) if current.get(p)!=merged.get(p)}
        result={'exit_code':0,'stdout':json.dumps({'process_id':task,'imported':sorted(changes),'published':False}),'stderr':'','changes':changes,'source_process':task,'provenance':{'source_task':task,'source_generation_sha256':digest(source),'source_through_event':max((e['id'] for e in self.store.events(task)),default=0),'reference_not_new_independent_source':True}}
        save(journal,{'phase':'IMPORTING','before':digest(current),'after':digest(merged),'temp':str(temp),'result':result})
        try:return self.reconcile_transfer(workspace,intent)
        except Exception as exc:raise UncertainOperation('Private transfer receipt requires reconciliation; no automatic replay: '+str(exc)) from exc
    @staticmethod
    def reconcile_transfer(workspace,intent):
        path=workspace.root/('transfer-'+str(intent)+'.json')
        if not path.exists():raise UncertainOperation('Import intent lacks a receipt; no automatic replay')
        row=json.loads(path.read_text());temp=Path(row['temp'])
        if temp!=workspace.root/('transfer-'+str(intent)):raise UncertainOperation('Invalid transfer path')
        now=digest(workspace.tree())
        if row['phase']=='IMPORTED':
            if now!=row['after']:raise UncertainOperation('Collected scratch changed before observation was saved')
        elif now==row['before'] and temp.exists() and digest(snapshot(temp))==row['after']:
            workspace.exchange(temp)
        elif now!=row['after']:raise UncertainOperation('Import generations do not reconcile')
        row['phase']='IMPORTED';save(path,row)
        if temp.exists():remove_private_tree(temp)
        return row['result']|{'reconciled':True}
    def reconcile(self,task):
        from neuro_next.persistent import launch,unit_state
        self.owned(task)
        if unit_state(task).get('ActiveState') in {'active','activating','deactivating'}:return self.poll(task)
        if not any(e['kind']=='intent' for e in self.store.events(task)):raise UncertainOperation('No recorded execution intent; old command is not started automatically')
        # The existing process_worker resumes recorded intents without executing
        # their command again. Missing trusted export remains UNCERTAIN there.
        launch(self.store,task);return self.poll(task)
    def stop_owned_by_parent(self):
        for row in self.store.db.execute('SELECT task FROM conversation_processes WHERE parent=?',(self.parent,)).fetchall():self.stop(row['task'])

    def wait(self,task,seconds=30,deadline=None,cancel=None):
        end=time.monotonic()+seconds
        if deadline is not None:end=min(end,deadline)
        while time.monotonic()<end:
            row=self.poll(task)
            if row['process_state']['state']!='RUNNING':return row
            if self.store.runtime(self.parent)['control'] in {'pause','cancel'} or cancel is not None and cancel.is_set():break
            time.sleep(.2)
        return self.poll(task)|{'bounded_wait':True,'waiting_is_not_goal_verification':True}

    def summaries(self):
        ids=self.ids();active=[t for t in ids if self.store.get(t)['status'] in {'ready','running','checking','repairing','uncertain'}]
        rows=[]
        for task in dict.fromkeys(active+ids[-3:]):
            s=self.poll(task)['process_state'];rows.append({k:s[k] for k in ['process_id','state','controller_status','exit_code','exit_status_known','stream_bytes','pty','timed_out','cancelled','configured_timeout_s','source_event']})
        return rows
