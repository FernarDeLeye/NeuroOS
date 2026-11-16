"""Actual assistant turns followed by host observations; no synthesized solutions.

Previous requests are retained verbatim for prefix reuse. Only recorded execution
or actual model text is represented as an assistant turn. Evidence remains data.
"""
import copy,json

class DialogueContextLimit(RuntimeError):pass

class DialogueHistoryError(RuntimeError):pass

def build_dialogue(store,task,system,context,max_chars=16000):
    flat=[{'role':'system','content':system},{'role':'user','content':json.dumps(context,ensure_ascii=False)}]
    events=store.events(task);models=[e for e in events if e['kind']=='model']
    if not models:
        if sum(len(m['content']) for m in flat)>max_chars:raise DialogueContextLimit('initial context budget exhausted')
        return flat
    last=models[-1];exchange=last['data'];prior=exchange.get('request',{}).get('messages')
    if not isinstance(prior,list) or len(prior)<2:raise DialogueHistoryError('request history unavailable; refusing dialogue reset')
    if prior[0]!={'role':'system','content':system} or any(m.get('role') not in {'user','assistant'} or not isinstance(m.get('content'),str) for m in prior[1:]):
        raise ValueError('untrusted or changed dialogue system/roles')
    newer=[e for e in events if e['id']>last['id']];action=exchange.get('action')
    executed=isinstance(action,dict) and any(
        (e['kind']=='intent' and e['data']==action) or
        (e['kind']=='claim' and {k:v for k,v in e['data'].items() if k!='verified'}==action) or
        (action.get('kind')=='recall' and e['kind']=='recall') for e in newer)
    choices=(exchange.get('response') or {}).get('choices') or []
    reply=choices[0].get('message',{}).get('content') if choices else None
    if not isinstance(reply,str) or not reply:
        reply=json.dumps(action,ensure_ascii=False) if executed else None
    messages=copy.deepcopy(prior)
    if reply is not None:messages.append({'role':'assistant','content':reply})
    state={k:v for k,v in context.items() if k!='recent'}
    state['events_since_last_reply']=[copy.deepcopy(e) for e in context.get('recent',[]) if e['id']>last['id']]
    state['last_action_executed']=bool(executed)
    current={'role':'user','content':json.dumps(state,ensure_ascii=False)}
    if messages[-1]['role']=='user':messages[-1]=current
    else:messages.append(current)
    # Keep the initial goal and the most recent response/observation pair. Old
    # turns stay available in Store/recall; never promote their text to system.
    dropped=0
    while sum(len(m['content']) for m in messages)>max_chars and len(messages)>4:
        del messages[2:4];dropped+=2
    if sum(len(m['content']) for m in messages)>max_chars:
        raise DialogueContextLimit('mandatory dialogue exceeds context budget; no flat fallback')
    if dropped:
        state['older_dialogue_messages_omitted']=dropped;messages[-1]['content']=json.dumps(state,ensure_ascii=False)
        if sum(len(m['content']) for m in messages)>max_chars:raise DialogueContextLimit('dialogue metadata exceeds context budget')
    return messages
