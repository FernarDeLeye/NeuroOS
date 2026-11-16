"""Reconcile isolated shell completion/import, never rerun an unknown command."""
import hashlib,json,os
from pathlib import Path
from .durable import save,sync_dir
from .workspace_tree import snapshot,metadata

class UncertainOperation(RuntimeError):pass

def digest(tree):return hashlib.sha256(json.dumps(metadata(tree),sort_keys=True).encode()).hexdigest()

class OperationJournal:
    def __init__(self,workspace,intent):
        if type(intent)is not int or intent<1:raise ValueError('positive intent ID required')
        self.workspace=workspace;self.path=workspace.root/('operation-'+str(intent)+'.json');self.intent=intent
    def write(self,**values):
        row=json.loads(self.path.read_text()) if self.path.exists() else {'intent_id':self.intent}
        row.update(values);save(self.path,row);return row
    def logs(self):return {k:self.workspace.root/('operation-'+str(self.intent)+'.'+k) for k in ['stdout','stderr']}
    def reconcile(self):
        from .execution_workspace import remove_private_tree
        if not self.path.exists():raise UncertainOperation('Intent has no operation receipt; command is not replayed')
        row=json.loads(self.path.read_text());unit=row['unit']
        if unit!='neuroos-workspace-'+row['token']+'.scope' or len(row['token'])!=32 or any(c not in '0123456789abcdef' for c in row['token']):raise UncertainOperation('invalid recorded scope identity')
        from .runtime_support import UserSystemd
        import subprocess
        bus=UserSystemd.connect();env=bus.environment()
        subprocess.run(['systemctl','--user','stop',unit],env=env,capture_output=True,timeout=10)
        probe=subprocess.run(['systemctl','--user','show',unit,'-p','ActiveState','-p','LoadState','-p','ControlGroup'],env=env,capture_output=True,text=True,timeout=5)
        props=dict(l.split('=',1) for l in probe.stdout.splitlines() if '=' in l)
        if props.get('ActiveState') in {'active','activating','deactivating','reloading'}:raise UncertainOperation('recorded worker has not stopped')
        # Failure to contact manager is not evidence of absence.
        if not props or (probe.returncode and props.get('LoadState')!='not-found'):raise UncertainOperation('cannot confirm worker absence')
        if row['phase']=='IMPORTED':
            if digest(self.workspace.tree())!=row.get('after_sha256'):raise UncertainOperation('imported scratch differs from durable receipt')
            old=self.workspace.root/('export-'+row['token']);root=self.workspace.root/('root-'+row['token'])
            if old.exists():remove_private_tree(old)
            if root.exists():root.rmdir()
            return row['result']
        if row['phase']=='FAILED':
            if digest(self.workspace.tree())!=row['before_sha256']:raise UncertainOperation('failed operation scratch differs from precondition')
            for name in ['export-','root-']:
                path=self.workspace.root/(name+row['token'])
                if path.exists():
                    if name=='export-':remove_private_tree(path)
                    else:path.rmdir()
            return row['result']
        report=self.workspace.root/('report-'+row['token']+'.json');export=self.workspace.root/('export-'+row['token'])
        proof=json.loads(report.read_text()) if report.exists() and report.stat().st_size else None
        if not proof or proof.get('export_complete') is not True or type(proof.get('exit_code'))is not int:raise UncertainOperation('No trusted completed export; transient effects/output are unknown; no replay')
        current=digest(self.workspace.tree());after=row.get('after_sha256')
        if after is None:
            if not export.exists() or current!=row['before_sha256']:raise UncertainOperation('cannot establish scratch generation')
            after=digest(snapshot(export));row=self.write(after_sha256=after)
        if current==after:
            imported=True
        elif current==row['before_sha256'] and export.exists() and digest(snapshot(export))==after:
            self.workspace.exchange(export);imported=True
        else:raise UncertainOperation('scratch/export differ from recorded generations')
        logs=self.logs();result=row.get('result') or {'exit_code':proof['exit_code'],**{k:p.read_text(errors='replace')[:262144] if p.exists() else '' for k,p in logs.items()},'output_may_be_incomplete':True,'timed_out':False,'output_limited':False}
        result.update(isolation=proof['isolation'],scope=unit,reconciled=True)
        if 'changes' not in result:
            current=metadata(self.workspace.tree());before=row['before']
            result['changes']={p:{'before':before.get(p),'after':current.get(p)} for p in sorted(before.keys()|current.keys()) if before.get(p)!=current.get(p)}
        self.write(phase='IMPORTED',result=result)
        if export.exists():remove_private_tree(export)
        root=self.workspace.root/('root-'+row['token'])
        if root.exists():root.rmdir()
        return result
