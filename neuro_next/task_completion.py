"""Deterministic transitions for explicitly assigned, independently checked outputs.

No command synthesis, goal interpretation, inferred permissions or expected-answer
injection. Existing Publisher controls collisions and conditional rollback.
"""
import hashlib,json
from .workspace_tree import logical_path,snapshot,metadata

class PreparedCompletion:
    def __init__(self,store,task,workspace,publisher,contract,protected,max_repairs):
        self.store,self.task,self.workspace,self.publisher=store,task,workspace,publisher
        self.contract,self.protected,self.max_repairs=contract,protected,max_repairs
        self.seen=set();self.attempts=0;self.repair_attempts=0;self.awaiting_repair=False;self.last_candidate=None;self.is_repair=False;self.phase='prepared_verification'
    def event(self,kind,data):return self.store.event(self.task,kind,data)
    @staticmethod
    def verdict(value):
        if not value.get('checks') or any(type(v) is not bool for v in value['checks'].values()):raise RuntimeError('invalid independent checks')
        value['pass']=all(value['checks'].values());return value
    def begin_candidate(self,source):
        conditions=getattr(self.contract,'conditions',[]);dependencies=set()
        for c in conditions:
            for key in ['path','source','before','after']:
                if key in c:
                    path=logical_path(c[key]);dependencies.add(path)
                    if key in {'before','after'}:dependencies.update(p for p in source if p.startswith(path+'/'))
        view=self.contract.progress_metadata(source) if hasattr(self.contract,'progress_metadata') else metadata(source)
        candidate=hashlib.sha256(json.dumps({p:view.get(p) for p in sorted(dependencies)},sort_keys=True).encode()).hexdigest()
        new_repair=self.awaiting_repair and candidate!=self.last_candidate
        self.is_repair=new_repair or (self.is_repair and candidate==self.last_candidate)
        if new_repair:
            self.repair_attempts+=1
            self.event('repair_attempt',{'number':self.repair_attempts,'candidate_sha256':candidate,'basis':'changed_goal_dependencies_after_negative_verification'})
        self.last_candidate=candidate
        if getattr(self.publisher,'persistent_lifecycle',False):self.event('candidate_state',{k:getattr(self,k) for k in ['last_candidate','repair_attempts','awaiting_repair','is_repair']})
    def finish_repair(self,passed):
        if self.is_repair:self.event('repair_result',{'number':self.repair_attempts,'pass':passed})
        self.is_repair=False
        if passed:self.awaiting_repair=False
    def make_feedback(self,verdict,category,source):
        from .goal_feedback import feedback
        return feedback(self.contract,verdict,self.publisher,self.workspace.scratch,category=category,source=source,remaining_repairs=max(0,self.max_repairs-self.repair_attempts))
    def publication_preflight(self,paths):
        # Validate selected formally specified artifacts, not unrelated unfinished
        # outputs. Unknown goals retain UNVERIFIED; no new inferred capability.
        selected=[logical_path(p) for p in paths]
        conditions=[c for c in self.contract.conditions if c.get('path') and any(c['path']==p or c['path'].startswith(p+'/') for p in selected)] if self.contract else []
        if not conditions:return None
        from .workspace_agent import WorkspaceContract
        verdict=WorkspaceContract(conditions).verify(self.workspace.scratch,self.store.events(self.task),{'report':''})
        self.event('publication_preflight',verdict|{'source':'scratch','paths':selected})
        if verdict['pass']:return None
        detail=self.make_feedback(verdict,'publication_goal_mismatch','scratch')
        self.event('controller_feedback',detail)
        return {'exit_code':126,'stdout':'','stderr':'Publication refused: selected artifact has independently observed goal mismatch. Correct scratch first.',
                'error':{'category':'goal_verification','nothing_published':True}}
    def check(self):
        if self.contract is None:return None
        conditions=getattr(self.contract,'conditions',[]);paths=set();destinations=set()
        for c in conditions:
            if c['kind'] not in {'json','sha256','tar_gz','moved','tree_diff_json'}:continue
            path=logical_path(c['path']);destinations.add(path);paths.add(path)
            if c['kind']=='moved':paths.add(logical_path(c['source']))
        if not destinations:return None  # exists alone and model claims never prove content
        source=snapshot(self.workspace.scratch);live=snapshot(self.publisher.live)
        if any(source.get(p,{}).get('kind')!='file' for p in destinations):return None
        unchanged=all(source.get(p)==live.get(p) for p in paths)
        # Only named result/condition paths and their ancestors determine a
        # candidate. Intermediate files are useful work, not a repair attempt.
        dependencies=set(paths)
        for condition in conditions:
            for key in ['path','source','before','after']:
                if key in condition:
                    root=logical_path(condition[key]);dependencies.add(root)
                    if key in {'before','after'}:dependencies.update(p for p in source if p.startswith(root+'/'))
        for path in list(dependencies):
            parts=path.split('/')
            dependencies.update('/'.join(parts[:i]) for i in range(1,len(parts)))
        view=self.contract.progress_metadata(source) if hasattr(self.contract,'progress_metadata') else metadata(source)
        self.phase='prepared_verification'
        prepared=self.verdict(self.contract.verify(self.workspace.scratch,self.store.events(self.task),{'report':''}))
        if unchanged and prepared['pass']:return None
        evidence_checks={k:v for k,v in prepared['checks'].items() if k.endswith(':execution_recovery')}
        live_view=metadata(live)
        footprint={'scratch':{p:view.get(p) for p in sorted(dependencies)},
                   'live':{p:live_view.get(p) for p in sorted(dependencies)},'execution_checks':evidence_checks}
        fingerprint=hashlib.sha256(json.dumps(footprint,sort_keys=True).encode()).hexdigest()
        if fingerprint in self.seen:return None
        self.seen.add(fingerprint);self.begin_candidate(source);self.attempts+=1
        self.event('prepared_verification',prepared|{'attempt':self.attempts,'source':'scratch','publication_performed_by_this_check':False,'controller_fingerprint':fingerprint,'candidate_sha256':self.last_candidate})
        if not prepared['pass']:
            return self.reject('prepared_postconditions',[k for k,v in prepared['checks'].items() if not v],prepared.get('measurements',{}))
        # Even an unambiguous destination must have explicit publication authority.
        for p in sorted(paths):
            if source.get(p) is None:
                authorized=p in self.publisher.policy.remove
            else:authorized=self.publisher.policy.can_create(p) or p in self.publisher.policy.edit
            if not authorized:return self.reject('publication_authority',[p])
        self.event('controller_transition',{'operation':'publish','paths':sorted(paths),'basis':'explicit_destinations_and_independent_scratch_checks'})
        self.phase='publication'
        try:receipt=self.publisher.publish(self.workspace.scratch,sorted(paths))
        except ValueError as exc:
            current=snapshot(self.publisher.live)
            detail={p:{'path':p,'mismatch_type':'publication_policy_rejection','live_path_present':p in current,'reason':str(exc)} for p in sorted(paths)}
            return self.reject('publication_policy',sorted(paths),detail)
        self.event('controller_publication',receipt)
        try:
            self.phase='verification'
            final=self.contract.verify(self.publisher.live,self.store.events(self.task),{'report':''})
            current=snapshot(self.publisher.live)
            final['checks']['protected_inputs']=all(current.get(p)==v for p,v in self.protected.items())
            final=self.verdict(final)
            self.event('external_verification',final|{'source':'live','initiator':'controller','attempt':self.attempts})
            if final['pass']:
                self.finish_repair(True);return {'verified':True,'receipt':receipt}
        except BaseException:
            failed_phase=self.phase;self.phase='rollback'
            self.event('rollback',self.publisher.rollback(receipt['transaction']));self.phase=failed_phase;raise
        self.phase='rollback'
        self.event('rollback',self.publisher.rollback(receipt['transaction']))
        return self.reject('live_postconditions',[k for k,v in final['checks'].items() if not v],final.get('measurements',{}))
    def reject(self,category,failed,measurements=None,source='scratch'):
        self.awaiting_repair=True;self.finish_repair(False)
        self.event('verification_failed',{'category':category,'failed':failed,'attempt':self.attempts,'repair_attempts':self.repair_attempts,'initiator':'controller'})
        verdict={'checks':{k:False for k in failed},'measurements':measurements or {}}
        feedback=self.make_feedback(verdict,category,source);feedback['attempt']=self.attempts
        self.event('controller_feedback',feedback)
        return {'verified':False,'exhausted':self.repair_attempts>=self.max_repairs,'feedback':feedback}
