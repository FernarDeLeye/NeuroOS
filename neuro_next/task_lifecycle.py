"""Persistent checkpoints over the existing kernel, Store and publication WAL."""
import json,os,time,signal
from pathlib import Path
from .durable import save
from .workspace_tree import snapshot,metadata
from .operation_journal import OperationJournal,UncertainOperation

class TaskStop(Exception):
    def __init__(self,status,reason):self.status,self.reason=status,reason;super().__init__(reason)

def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

class TaskLifecycle:
    def __init__(self,store,task,*,slice_actions=None,reconcile_only=False,discard_unconfirmed=False):
        self.store,self.task=store,task;registered=store.specification(task)
        if not registered:raise ValueError('task has no persistent specification')
        self.reconcile_only=reconcile_only;self.discard_unconfirmed=discard_unconfirmed
        self.options=registered['options'];self.slice_actions=slice_actions;self.actions=0;self.processed=0
        self.checkpoint=store.runtime(task)['checkpoint'];self.resuming=bool(store.runtime(task)['run'] or any(e['kind']=='workspace_contract' for e in store.events(task)));self.workspace=None;self.publisher=None
    def stage(self,value):self.store.runtime_update(self.task,stage=value)
    def control(self):return self.store.runtime(self.task)['control']
    def stop_requested(self):
        requested=self.control()
        if requested in {'pause','cancel'}:raise TaskStop('paused' if requested=='pause' else 'cancelled','Operator requested '+requested+'; no future action executed.')
    def hook(self,point):
        fault=self.options.get('test_stop_at')
        if fault==point and not any(e['kind']=='fault_injected' for e in self.store.events(self.task)):
            self.store.event(self.task,'fault_injected',{'point':point,'controller_only':True});os.kill(os.getpid(),signal.SIGKILL)
        if point=='observation_recorded':
            self.actions+=1
            if self.slice_actions and self.actions>=self.slice_actions:raise TaskStop('paused','Action slice completed; effects and observation saved.')
            self.stop_requested()
    def ensure_scratch(self,root):
        if any(e['kind']=='intent' for e in self.store.events(self.task)) and not (Path(root)/'scratch').is_dir():raise TaskStop('uncertain','Previously used scratch is absent; no reconstruction or replay performed.')
    def prepare(self,workspace,publisher):
        self.workspace,self.publisher=workspace,publisher;path=self.store.root/(self.task+'.baseline.json')
        if not path.exists():
            if any(e['kind']=='intent' for e in self.store.events(self.task)):raise TaskStop('uncertain','Original publication baseline is absent')
            save(path,snapshot(publisher.live))
            if metadata(workspace.tree())!=metadata(snapshot(publisher.live)):raise TaskStop('uncertain','Incomplete initial scratch snapshot')
        self.baseline=json.loads(path.read_text());self.stage('RECONCILING')
        try:
            rolled=publisher.recover()
            for receipt in rolled:self.store.event(self.task,'rollback',receipt|{'initiator':'reconciliation'})
            records=publisher.records()
            expected=dict(self.baseline)
            for p,v in publisher.known().items():
                if v is None:expected.pop(p,None)
                else:expected[p]=v
            if snapshot(publisher.live)!=expected:raise UncertainOperation('Live data differs from the original baseline and publication journal; no adoption or replay')
            for p,identity in publisher.known_identities().items():
                if publisher._identity(p)!=identity:raise UncertainOperation('Published object identity changed outside the task: '+p)
            publisher.initial_snapshot=self.baseline
            self.retry_publication=bool(records and records[-1]['state']=='rolled_back')
            events=self.store.events(self.task);observed={e['data'].get('intent_id') for e in events if e['kind']=='observation'}
            collection_plans={e['data']['intent_id']:e['data']['basis'] for e in events if e['kind']=='private_collection_planned'}
            for event in events:
                if event['kind']!='intent' or event['id'] in observed:continue
                if event['data']['kind']=='shell':
                    journal=OperationJournal(workspace,event['id'])
                    try:result=journal.reconcile()
                    except UncertainOperation:
                        if not self.discard_unconfirmed:raise
                        from .operation_journal import digest
                        prior=json.loads(journal.path.read_text()) if journal.path.exists() else {}
                        before=prior.get('before_sha256',self.checkpoint.get('workspace_digest'))
                        if prior.get('phase') not in {None,'RUNNING','IMPORTING'} or digest(workspace.tree())!=before:raise UncertainOperation('Cannot discard: scratch may have imported effects')
                        result={'exit_code':None,'exit_status_known':False,'stdout':'','stderr':'Operator discarded unconfirmed transient work; no old action replay.','error':{'category':'abandoned_unconfirmed'},'reconciled':True}
                        journal.write(phase='ABANDONED',result=result)
                        self.store.event(self.task,'operator_discard',{'intent_id':event['id'],'unconfirmed_transient_work_discarded':True,'no_imported_effects':True})
                elif event['data']['kind']=='artifact_import':
                    from .agent_processes import AgentProcesses
                    result=AgentProcesses.reconcile_transfer(workspace,event['id'])
                    if event['id'] in collection_plans:result['basis']=collection_plans[event['id']]
                elif event['data']['kind']=='process_start':
                    row=self.store.db.execute('SELECT task FROM conversation_processes WHERE parent=? AND intent=?',(self.task,event['id'])).fetchone()
                    if not row:raise UncertainOperation('Process launch has no registered child; no automatic relaunch')
                    from .agent_processes import AgentProcesses
                    manager=AgentProcesses(self.store,self.options['conversation'],self.task);result=manager.poll(row['task'])
                elif event['data']['kind']=='process_input':
                    target=event['data'].get('process_id',event['data'].get('task_id'));request_id=self.task+':'+str(event['id'])
                    recorded=any(e['kind']=='process_input' and e['data'].get('request_id')==request_id for e in self.store.events(target))
                    if not recorded:raise UncertainOperation('PTY input delivery is uncertain; inspect process output, never resend automatically')
                    result={'exit_code':0,'stdout':json.dumps({'queued':True,'reconciled_transport_only':True}),'stderr':''}
                elif event['data']['kind'] in {'process_stop','process_reconcile'}:
                    from .agent_processes import AgentProcesses
                    manager=AgentProcesses(self.store,self.options['conversation'],self.task);result=manager.stop(event['data'].get('process_id',event['data'].get('task_id'))) if event['data']['kind']=='process_stop' else manager.reconcile(event['data'].get('process_id',event['data'].get('task_id')))
                elif event['data']['kind'] in {'system','history','task_status','past_observation','process_status','process_wait'}:
                    result={'exit_code':None,'stdout':'','stderr':'Read-only observation was interrupted; no previous command replay.','exit_status_known':False,'reconciled':True}
                elif event['data']['kind'] in {'task_control','permission','workspace'}:raise UncertainOperation('Task control outcome not recorded; inspect target task, no automatic replay')
                elif event['data']['kind']=='publish':
                    matches=[r for r in records if r.get('operation_id')==event['id']]
                    if not matches:raise UncertainOperation('Publication intent has no linked durable transaction; no automatic replay')
                    if matches[-1]['state']=='committed':result={'exit_code':0,'stdout':json.dumps({'transaction':matches[-1]['id'],'publication_integrity_verified':True,'task_verification_granted':False}),'stderr':'','reconciled':True}
                    elif matches[-1]['state']=='rolled_back':result={'exit_code':125,'stdout':'','stderr':'Interrupted publication was conditionally rolled back. No command replay.','reconciled':True}
                    else:raise UncertainOperation('Publication outcome unresolved')
                else:continue
                self.store.event(self.task,'observation',result|{'intent_id':event['id'],'reconciled':True})
            if self.resuming:self.store.event(self.task,'controller_feedback',{'category':'task_reconciled','message':'Prior recorded commands are not replayed. Continue from actual observations and current scratch. Publication WAL reconciled.','rolled_back_transactions':[r['transaction'] for r in rolled]})
        except (UncertainOperation,ValueError,OSError) as exc:raise TaskStop('uncertain',str(exc)) from exc
        run=self.store.runtime(self.task)['run'];spent=0
        if run:
            spent=run.get('spent_total',run.get('spent_before',0))
            if run.get('active'):spent=run['spent_before']+min(run['allocation'],max(0,time.monotonic()-run['start']) if run['boot']==boot() else run['allocation'])
        remaining=self.options['seconds']-spent
        remaining=max(0,remaining)
        self.run={'active':True,'pid':os.getpid(),'pid_start':Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19],'pid_namespace':os.readlink('/proc/self/ns/pid'),'boot':boot(),'start':time.monotonic(),'spent_before':spent,'allocation':remaining}
        self.store.runtime_update(self.task,run=self.run,stage='RUNNING')
        return self.baseline,remaining
    def finish_run(self):
        run=self.store.runtime(self.task)['run']
        if run and run.get('active'):
            charged=min(run['allocation'],max(0,time.monotonic()-run['start']) if run['boot']==boot() else run['allocation'])
            self.store.runtime_update(self.task,run={'active':False,'spent_total':run['spent_before']+charged})
    def restore(self,completion,state_progress,progress,local):
        c=self.checkpoint
        for key,value in c.get('local',{}).items():local[key]=value
        if completion:
            for k,v in c.get('completion',{}).items():setattr(completion,k,set(v) if k=='seen' else v)
        for k,v in c.get('state_progress',{}).items():setattr(state_progress,k,set(v) if k in {'seen','knowledge_seen'} else v)
        ep=c.get('evidence_progress',{});progress.stagnant=ep.get('stagnant',0);progress.ranges={tuple(k):v for k,v in ep.get('ranges',[])}
        self.processed=c.get('processed',0)
        for event in self.store.events(self.task):
            if event['id']<=c.get('through_event',0):continue
            d=event['data'];kind=event['kind']
            if completion and kind=='candidate_state':
                for key in ['last_candidate','repair_attempts','awaiting_repair','is_repair']:setattr(completion,key,d[key])
            if completion and kind=='prepared_verification':
                completion.attempts=max(completion.attempts,d['attempt']);completion.seen.add(d['controller_fingerprint'])
            if completion and kind=='verification_failed':completion.awaiting_repair=True
            if completion and kind=='repair_result':completion.is_repair=False;completion.awaiting_repair=not d['pass']
            if kind=='verification_cycle':local.update({k:d[k] for k in ['checks','last_failure','stagnant_checks']})
            if kind=='progress_assessment':
                if d.get('useful_information') and d.get('knowledge_fact_sha256'):state_progress.knowledge_seen.add(d['knowledge_fact_sha256'])
                state_progress.seen.add(d['state_sha256']);state_progress.previous=d['state_sha256'];state_progress.previous_raw=d['raw_state_sha256'];state_progress.stagnant=d['stagnant_actions'];self.processed=max(self.processed,d['event_id'])
            if kind=='recall':progress.recall(d)
            if kind=='intent':local['errors']=0
            if kind=='model_error' and d.get('category')=='protocol_or_path':local['errors']+=1
            if kind=='tool_failure':local['failed_commands'].append(d['intent_id'])
            if kind=='command_recovery':local['failed_commands']=[]
        if completion and getattr(self,'retry_publication',False):
            positives={e['data']['controller_fingerprint'] for e in self.store.events(self.task) if e['kind']=='prepared_verification' and e['data']['pass'] and 'controller_fingerprint' in e['data']}
            completion.seen-=positives
        return local
    def save(self,completion,state_progress,progress,local):
        es=self.store.events(self.task)
        from .operation_journal import digest
        data={'workspace_digest':digest(self.workspace.tree()),'through_event':es[-1]['id'] if es else 0,'processed':self.processed,'local':local,
              'state_progress':{k:list(getattr(state_progress,k)) if k in {'seen','knowledge_seen'} else getattr(state_progress,k) for k in ['seen','knowledge_seen','previous','previous_raw','stagnant']},
              'evidence_progress':{'stagnant':progress.stagnant,'ranges':[[list(k),v] for k,v in progress.ranges.items()]}}
        if completion:data['completion']={k:list(getattr(completion,k)) if k=='seen' else getattr(completion,k) for k in ['seen','attempts','repair_attempts','awaiting_repair','last_candidate','is_repair']}
        self.store.runtime_update(self.task,checkpoint=data)
    def pending(self):return [e for e in self.store.events(self.task) if e['kind']=='observation' and e['id']>self.processed]
    def intent(self,action):
        # Control requests and action admission serialize in the same database.
        with self.store.db:
            self.stop_requested()
            row=self.store.db.execute('INSERT INTO events(task,kind,data,created) VALUES(?,?,?,?)',(self.task,'intent',json.dumps(action),time.time()))
            return row.lastrowid
    def live_satisfied(self,contract,protected,completion):
        if not self.resuming or contract is None or not any(r['state']=='committed' for r in self.publisher.records()):return False
        if not any(c['kind'] in {'json','sha256','moved','tar_gz','tree_diff_json'} for c in contract.conditions):return False
        verdict=contract.verify(self.publisher.live,self.store.events(self.task),{'report':''});current=snapshot(self.publisher.live)
        verdict['checks']['protected_inputs']=all(current.get(p)==v for p,v in protected.items());verdict['pass']=all(verdict['checks'].values())
        self.store.event(self.task,'external_verification',verdict|{'source':'live','initiator':'reconciliation'})
        if verdict['pass']:
            if completion:completion.finish_repair(True)
            return True
        return False
    def remaining_steps(self):return self.options['steps']-sum(e['kind']=='inference_started' for e in self.store.events(self.task))
