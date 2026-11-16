"""Model-selected conversational subjects, validated against delivered observations.

This module validates identities, not natural-language intent. No keyword router.
"""
import re
from .conversation_refs import displayed,remember

INSTRUCTIONS='''
The final current_user_request is the ONLY active user goal. Earlier requests and
focus are historical candidates, not instructions to repeat. A new topic can require
scope:new even when an old focus exists.
Before choosing an action, identify what THIS utterance refers to in target:
{ref:"VIEW:POSITION",freshness:current|historical} for a previously shown object;
{scope:new} for a genuinely new question/list; {scope:uncertain} if ambiguous.
This is YOUR language interpretation, not evidence of current state. Resolve first,
second,last against PRINTED positions and the intended earlier list, not new rankings.
Names can repeat; use the supplied exact identity/ref. Past references stay addressable across topics, but are not automatically current targets.
query requires an explicit continue boolean: false ONLY if its factual display
satisfies the CURRENT user goal; true for preliminary observations before more work.
For an object target, query has NO subject/freshness fields: target already
selects the only object and freshness. Never substitute a new source=list. Current queries independently check identity; historical queries retain time.
For purpose/explanation first query that ref with continue=true if further reasoning
is needed; do not ask for a name that is already in memory. If context is insufficient,
use memory to retrieve the original records. Uncertain target requires clarification.
'''

def schema(refs=None,flat=False):
 result= {'oneOf':[
  {'type':'object','properties':{'ref':({'type':'string','pattern':'^[1-9][0-9]*:[1-9][0-9]*$'} if refs is None else {'type':'string','enum':list(refs)}),'freshness':{'type':'string','enum':['current','historical']}},'required':['ref','freshness'],'additionalProperties':False},
  {'type':'object','properties':{'scope':{'type':'string','enum':['new','uncertain']}},'required':['scope'],'additionalProperties':False}]}
 if flat:
  obj=result['oneOf'][0];obj['properties']={'scope':{'type':'string','enum':['object']},**obj['properties']};obj['required']=['scope',*obj['required']]
 if refs==[]:result['oneOf']=result['oneOf'][1:]
 return result

def validate(runtime,action):
 if runtime.version>=7:
  from .reference_policy import resolution
  unresolved=resolution(runtime)
  if unresolved and not (action['kind']=='memory' or action['kind']=='reply' and action.get('basis')=='clarification'):
   raise ValueError('Unresolved reference: '+str(unresolved)+'. Ask a short clarification, do not choose a list member. Nothing executed.')
 target=action.get('target')
 if not isinstance(target,dict):raise ValueError('Select target before action: saved ref, new question, or uncertain')
 if runtime.version>=7 and action['kind']=='query' and target.get('scope')=='new':
  if type(action.get('limit'))is not int or not 0<=action['limit']<=4096:raise ValueError('Choose explicit presentation limit: requested count, or0 for all; full source is retained')
 if runtime.version>=6:
  if action['kind']=='query':
   if type(action.get('continue'))is not bool:raise ValueError('Query needs explicit continue decision')
   if target.get('scope')=='new' and 'ref' in action.get('subject',{}):raise ValueError('An object query requires scope:object and its exact ref')
  if 'ref' in target and target.get('scope')!='object':raise ValueError('Object target needs explicit scope:object')
 if target.get('scope')=='object':target={k:v for k,v in target.items() if k!='scope'}
 if set(target)=={'scope'} and target['scope'] in {'new','uncertain'}:
  if target['scope']=='uncertain' and not (action['kind']=='reply' and action.get('basis')=='clarification' or action['kind']=='memory'):
   raise ValueError('Ambiguous target: ask a short clarification or retrieve exact memory; nothing executed')
  return None
 if set(target)!={'ref','freshness'} or not isinstance(target['ref'],str) or not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*',target['ref']) or target['freshness'] not in {'current','historical'}:raise ValueError('Invalid target identity/freshness')
 view,position=map(int,target['ref'].split(':'))
 fact=remember(runtime.store,runtime.id,{'view_event':view,'position':position})
 if action['kind']=='query' and (action['subject']!={'ref':target['ref']} or action['freshness']!=target['freshness']):
  raise ValueError('Target/action mismatch: selected '+target['ref']+'; query that exact ref/freshness, not another object or new ranking. Nothing executed.')
 if action['kind']=='reply' and action.get('basis')!='clarification' and target['freshness']=='current':
  checks=[e['data'] for e in runtime.store.events(runtime.task) if e['kind']=='reference_selection']
  if not any(c['view_event']==view and c['position']==position and c['status']!='HISTORICAL_ONLY' for c in checks):raise ValueError('Current object answer needs query of the selected ref first; old identity is not a current observation')
 return fact

def record(runtime,action,intent):
 fact=validate(runtime,action)
 prior=next((e for e in runtime.store.events(runtime.task) if e['kind']=='object_binding' and e['data'].get('intent_id')==intent),None)
 if prior:return prior['data']
 value={'intent_id':intent,'target':action['target'],'interpretation_by':'model','state_at_selection':'NOT_YET_CHECKED'}
 if fact:
  delivery=runtime.store.db.execute('SELECT message FROM conversation_view_deliveries WHERE view_event=? AND conversation=?',(fact['view_event'],runtime.id)).fetchone()
  value.update({k:fact[k] for k in ['view_event','position','source_event','source','row_index','original','original_observed_at']});value['delivered_message']=delivery['message']
 runtime.store.event(runtime.task,'object_binding',value)
 return value

def card(runtime,view_id,position,row):
 value=displayed(runtime.store,runtime.id,view_id)
 delivery=runtime.store.db.execute('SELECT message FROM conversation_view_deliveries WHERE view_event=? AND conversation=?',(view_id,runtime.id)).fetchone()
 result={'ref':str(view_id)+':'+str(position),'type':value['source'],'source_event':value['source_event'],'message':delivery['message'],'observed_at':value['observed_at'],'current_state':'NOT_CHECKED'}
 if value['source'].startswith('processes_'):result.update(name=row.get('name'),identity=row.get('identity'),pid=row['pid'])
 elif value['source']=='files':result.update({k:row[k] for k in ['path','kind','device','inode','sha256','size','mtime_ns','ctime_ns'] if k in row})
 elif value['source']=='wifi':result.update({k:row[k] for k in ['ssid','bssid','signal','security'] if k in row})
 else:result.update(row)
 return result


def prepare(runtime,action,intent,workspace=None,deadline=None,cancel=None):
 value=record(runtime,action,intent);target=action['target']
 if 'ref' in target and action['kind'] not in {'query','memory','reply'}:
  view,position=map(int,target['ref'].split(':'))
  check=runtime._execute({'kind':'inspect' if target['freshness']=='current' else 'remember','view_event':view,'position':position,'continue':True},workspace,intent,deadline,cancel)
  if check.get('reference',{}).get('status') in {'GONE','REPLACED','UNCERTAIN','CHANGED','NOT_OBSERVED'}:
   return {'exit_code':126,'stdout':'','stderr':'Bound object no longer confirmed; query its exact ref before deciding again.','reference':check['reference']}
 return None
