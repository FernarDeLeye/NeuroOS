"""Declarative directory-difference report predicate over bounded snapshots."""
import base64
from .reliability import strict_json
from .semantics import relative_path


def tree_difference(tree, condition):
    roots=[condition['before'],condition['after']]
    if any(tree.get(p,{}).get('kind')!='dir' for p in roots):return False,{'path':condition['path'],'mismatch_type':'source_directory_missing'}
    inputs=[]
    for root in roots:
        entries={p[len(root)+1:]:v for p,v in tree.items() if p.startswith(root+'/') and v['kind']!='dir'}
        if any(v['kind']!='file' for v in entries.values()):return False,{'path':condition['path'],'mismatch_type':'nonregular_source'}
        inputs.append(entries)
    before,after=inputs;common=before.keys()&after.keys()
    expected={'added':sorted(after.keys()-before.keys()),'removed':sorted(before.keys()-after.keys()),
              'changed':sorted(p for p in common if before[p]['sha256']!=after[p]['sha256']),
              'unchanged':sorted(p for p in common if before[p]['sha256']==after[p]['sha256'])}
    entry=tree.get(condition['path']);facts=[];invalid=None
    try:
        actual=strict_json(base64.b64decode(entry['data'])) if entry and entry['kind']=='file' else None
        valid=isinstance(actual,dict) and actual.keys()==expected.keys() and all(isinstance(actual[k],list) and all(isinstance(p,str) for p in actual[k]) for k in expected)
        if not valid:invalid='report_structure'
        else:
            actual={k:[relative_path(p) for p in values] for k,values in actual.items()}
            for category in expected:
                for path in actual[category]:
                    if path not in expected[category]:
                        a,b=before.get(path),after.get(path)
                        facts.append({'path':path,'claimed_category':category,'before_exists':a is not None,'after_exists':b is not None,
                            'before_size':a.get('size') if a else None,'after_size':b.get('size') if b else None,
                            'bytes_equal':a['sha256']==b['sha256'] if a and b else None})
            if actual!=expected:invalid='content_classification_or_coverage'
        return invalid is None,{'path':condition['path'],'mismatch_type':invalid,'comparisons':facts[:8],
                               'coverage_complete':valid and set(p for values in actual.values() for p in values)==before.keys()|after.keys(),
                               'sorted_unique_lists':{k:values==sorted(set(values)) for k,values in actual.items()} if valid else {},
                               'unreported_source_paths':sorted((before.keys()|after.keys())-set(p for values in actual.values() for p in values))[:8] if valid else [],
                               'basis':'SHA256 of actual file bytes; sizes and timestamps do not establish equality'}
    except (ValueError,TypeError,KeyError,UnicodeError):return False,{'path':condition['path'],'mismatch_type':'invalid_json'}
