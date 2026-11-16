"""Versioned semantic comparison helpers; never rewrites historical scores."""
import math

def relative_path(value):
    if not isinstance(value,str) or not value or '\x00' in value or value.startswith('/'):
        raise ValueError('relative path required')
    parts=value.split('/')
    if '..' in parts:raise ValueError('parent traversal forbidden')
    normalized='/'.join(p for p in parts if p not in {'','.'})
    if not normalized:raise ValueError('file path required')
    return normalized

def json_equivalent(actual,expected):
    if isinstance(expected,bool) or isinstance(actual,bool):return type(actual) is type(expected) and actual==expected
    if isinstance(actual,(int,float)) and isinstance(expected,(int,float)):
        return math.isfinite(actual) and math.isfinite(expected) and actual==expected
    if type(actual) is not type(expected):return False
    if isinstance(actual,dict):return actual.keys()==expected.keys() and all(json_equivalent(actual[k],v) for k,v in expected.items())
    if isinstance(actual,list):return len(actual)==len(expected) and all(json_equivalent(a,b) for a,b in zip(actual,expected))
    return actual==expected

def normalize_paths(value,keys):
    if isinstance(value,list):return [normalize_paths(v,keys) for v in value]
    if isinstance(value,dict):return {k:relative_path(v) if k in keys else normalize_paths(v,keys) for k,v in value.items()}
    return value
