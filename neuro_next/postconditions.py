"""Trusted declarative checks over real files and task-scoped observations.

The model cannot supply/replace this contract or run arbitrary verifier code.
Unsupported or absent postconditions remain UNVERIFIED. Saved data is not proof
of current filesystem freshness. Verification never executes artifact programs.
"""
import copy,hashlib,json,os,stat
from pathlib import Path
from .safe_files import SafeFiles,path_name
from .reliability import strict_json
from .evidence import resolve_evidence
from .semantics import json_equivalent,normalize_paths

class Contract:
    def __init__(self,conditions):
        if not isinstance(conditions,list) or not 1<=len(conditions)<=32:raise ValueError('nonempty bounded conditions required')
        self.conditions=copy.deepcopy(conditions)
        keys={'exists':{'kind','path'},'sha256':{'kind','path','sha256'},
              'json':{'kind','path','equals','path_keys'},'preserved':{'kind','path','sha256'},
              'moved':{'kind','path','source','sha256'},'saved_answer':{'kind','event_id','field'}}
        for c in self.conditions:
            if not isinstance(c,dict) or c.get('kind') not in keys or set(c)-keys[c['kind']]:raise ValueError('unknown postcondition')
            kind=c['kind'];required=keys[kind]-({'path_keys'} if kind=='json' else set())
            if not required<=c.keys():raise ValueError('postcondition fields missing')
            if kind!='saved_answer':path_name(c['path'])
            if kind=='moved':path_name(c['source'])
            if kind in {'sha256','preserved','moved'}:
                h=c['sha256']
                if not isinstance(h,str) or len(h)!=64 or any(x not in '0123456789abcdef' for x in h):raise ValueError('invalid hash')
            if kind=='json':
                if not isinstance(c.get('path_keys',[]),list) or any(not isinstance(k,str) for k in c.get('path_keys',[])):raise ValueError('invalid path keys')
                json.dumps(c['equals'],allow_nan=False)
            if kind=='saved_answer' and (type(c['event_id']) is not int or c['event_id']<1 or not isinstance(c['field'],str)):raise ValueError('invalid saved evidence')
    def describe(self):
        # Values are hidden from the model. The user goal supplies the requested
        # content; feedback reports which predicates failed, never hidden answers.
        return [{k:v for k,v in c.items() if k not in {'equals','sha256'}} for c in self.conditions]
    def verify(self,workspace,events,action):
        checks={};measurements={}
        class Recorded:
            def get_event(self,task,id):return next((e for e in events if e['id']==id and e['task']==task and e['kind']!='model'),None)
        # Same no-follow fd reader, with no journal or mutation capability.
        reader=object.__new__(SafeFiles);reader.work=Path(workspace).resolve()
        for i,c in enumerate(self.conditions):
            label=str(i)+':'+c['kind'];kind=c['kind']
            try:
                if kind=='saved_answer':
                    roots=[e for e in events if e['id']==c['event_id'] and e['kind']=='saved_observation']
                    if not roots:checks[label]=False;measurements[label]={'missing_source':True};continue
                    e=roots[0];fact=resolve_evidence(Recorded(),e['task'],e['id'])
                    value=e['data']['observation'].get(c['field'])
                    checks[label]=isinstance(value,str) and action.get('report')==value
                    measurements[label]={'root_event_id':fact['root_event_id'],'fact_sha256':fact['fact_sha256']}
                    continue
                snapshot=reader.snapshot(c['path']);measurements[label]={'path':c['path'],'sha256':snapshot['sha256'] if snapshot else None}
                if kind=='exists':checks[label]=snapshot is not None
                elif kind in {'sha256','preserved'}:checks[label]=snapshot is not None and snapshot['sha256']==c['sha256']
                elif kind=='moved':
                    # lexists catches dangling links: their presence is not a move.
                    with reader.parent(c['source']) as (fd,leaf):
                        try:os.stat(leaf,dir_fd=fd,follow_symlinks=False);absent=False
                        except FileNotFoundError:absent=True
                    checks[label]=absent and snapshot is not None and snapshot['sha256']==c['sha256']
                elif kind=='json':
                    import base64
                    actual=strict_json(base64.b64decode(snapshot['data'])) if snapshot else None
                    keys=c.get('path_keys',[])
                    checks[label]=snapshot is not None and json_equivalent(normalize_paths(actual,keys),normalize_paths(c['equals'],keys))
            except (ValueError,KeyError,UnicodeError,TypeError) as exc:
                checks[label]=False;measurements[label]={'invalid_state':type(exc).__name__}
            # Unexpected I/O exceptions escape: checker/runtime failures must not
            # be relabelled as a model's negative postcondition.
        return {'pass':all(checks.values()),'checks':checks,'measurements':measurements,'source':'linux-state-and-original-evidence'}
