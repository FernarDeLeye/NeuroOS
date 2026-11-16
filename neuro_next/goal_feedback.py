"""Facts from independent predicates, never commands or hidden answer values."""
import copy
from .semantics import json_equivalent


def mismatch_locations(actual, expected, prefix='$', limit=8):
    result=[]
    def visit(a,b,p):
        if len(result)>=limit or json_equivalent(a,b):return
        if type(a)!=type(b):result.append({'location':p,'type':'type_mismatch','actual_type':type(a).__name__,'required_type':type(b).__name__});return
        if isinstance(a,dict):
            for k in sorted(a.keys()|b.keys()):
                if k not in a or k not in b:result.append({'location':p+'.'+str(k),'type':'missing_field' if k not in a else 'unexpected_field'})
                else:visit(a[k],b[k],p+'.'+str(k))
                if len(result)>=limit:break
        elif isinstance(a,list):
            if len(a)!=len(b):result.append({'location':p,'type':'length_mismatch','actual_length':len(a),'required_length':len(b)})
            for i,(x,y) in enumerate(zip(a,b)):visit(x,y,p+'['+str(i)+']')
        elif a!=b:result.append({'location':p,'type':'content_mismatch'})
    visit(actual,expected,prefix)
    return result[:limit]


def feedback(contract, verdict, publisher, scratch, *, category, source, remaining_repairs):
    from .workspace_tree import snapshot
    live=snapshot(publisher.live);known=publisher.known();items=[]
    for label,passed in verdict['checks'].items():
        if passed:continue
        observed=copy.deepcopy(verdict.get('measurements',{}).get(label,{}));kind=label.split(':')[-1]
        condition=next((c for c in contract.conditions if c['kind']==kind and c.get('path')==observed.get('path')),None)
        if condition is None and category in {'publication_authority','publication_policy'}:
            condition=next((c for c in contract.conditions if c.get('path')==label or c.get('source')==label),None)
            if condition:
                observed.setdefault('path',label)
                if category=='publication_authority':observed['mismatch_type']='publication_permission_missing'
        condition=condition or {'kind':kind}
        requirement={k:v for k,v in condition.items() if k not in {'equals','sha256','members'}}
        if kind=='tar_gz':requirement['member_paths']=sorted(condition.get('members',{}))[:16]
        path=condition.get('path');grant=bool(path and (publisher.policy.can_create(path) or path in publisher.policy.edit or path in known))
        collision=bool(path in live and path not in publisher.policy.edit and path not in known)
        if kind=='moved':grant=grant and condition['source'] in publisher.policy.remove
        # Scope this to grants/collisions, never promise a future race-free commit.
        ability={'scratch_execution_available':True,'publication_grant_present':grant,'existing_destination_collision':collision,
                 'can_attempt_with_current_grants':grant and not collision,'publication_rechecks_required':True}
        if not grant:ability['obstacle']='publication_permission_missing'
        elif collision:ability['obstacle']='existing_destination_not_owned_by_task'
        # Full independent measurements remain in verification events. Bound the
        # actionable view, retaining member/path errors rather than 128 hashes.
        if 'members' in observed:observed['members']=list(observed['members'])[:16]
        items.append({'check':label,'requirement':requirement,'mismatch_type':observed.get('mismatch_type','postcondition_not_satisfied'),
                      'observed':observed,'evidence':{'source':source,'basis':'fresh_linux_snapshot'},'repairability':ability})
    return {'category':category,'failed':[k for k,v in verdict['checks'].items() if not v],'mismatches':items[:8],
            'remaining_repairs':remaining_repairs,'message':'The goal is not verified. Use the observed mismatch to inspect and correct the actual workspace; a successful command or publication alone is not completion.'}
