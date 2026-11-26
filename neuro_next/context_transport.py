"""Compact inference transport over exact durable conversation records."""
import copy,json
STATE_FIELDS=('publish_paths','workspace','permissions','cwd','managed_processes','postconditions')
def same_json(a,b):
    # Python equality aliases bool/int (True == 1). Constraints must not.
    try:return json.dumps(a,sort_keys=True,ensure_ascii=False,allow_nan=False)==json.dumps(b,sort_keys=True,ensure_ascii=False,allow_nan=False)
    except (TypeError,ValueError):return False

def compact_memory(value):
    memory=copy.deepcopy(value)
    for view in memory.get('lists',[]):
        objects=view.get('objects',[])
        if len(objects)<2:continue
        common={k:objects[0][k] for k in ('type','source_event','message','observed_at','current_state')
                if k in objects[0] and all(k in o and same_json(o[k],objects[0][k]) for o in objects)}
        if common:
            view['shared_object_fields']=common
            for obj in objects:
                for k in common:obj.pop(k)
    return memory

def compact(messages):
    result=copy.deepcopy(messages)
    index=len(result)-2
    if index<2 or result[index]['role']!='user':return result
    try:packet=json.loads(result[index]['content'])
    except ValueError:return result
    if not isinstance(packet,dict) or 'memory' not in packet:return result
    known={}
    for message in result[2:index]:
        if message['role']!='user':continue
        try:old=json.loads(message['content'])
        except ValueError:continue
        if isinstance(old,dict):
            for k in STATE_FIELDS:
                if k in old:known[k]=old[k]
    inherited=[]
    for k in STATE_FIELDS:
        if k in packet and k in known and same_json(packet[k],known[k]):
            del packet[k];inherited.append(k)
    if inherited:packet['unchanged_from_prior_host_packets']=inherited
    packet['memory']=compact_memory(packet['memory'])
    events=packet.get('results',[])
    checks={r['ref']:r for r in events if r.get('kind')=='reference_selection'}
    for item in events:
        data=item.get('data',{})
        ref=data.get('reference')
        if not isinstance(ref,dict):continue
        key=str(ref.get('view_event'))+':'+str(ref.get('position'))
        selection=checks.get(key)
        if selection and all(same_json(ref.get(k),selection.get(k)) for k in ('status','checked_at','current')):
            # Retain ALL fields unique to the observation. The removed values
            # exist verbatim in this SAME packet, not inferred from stale memory.
            ref={k:v for k,v in ref.items() if k not in ('status','checked_at','current')}
            data['reference']=dict(ref,selection_event=selection['event'])
    # Initial RUNNING report is controller boilerplate, not a model action/result.
    # Keep its durable event handle; completed/failed/verification states stay exact.
    for item in events:
        d=item.get('data',{})
        if item.get('kind')=='completion_state' and d=={'execution_status':'RUNNING','verification_status':'UNVERIFIED','report':'Execution started in rootless workspace.'}:
            item.pop('data');item['state']='RUNNING'
    result[index]['content']=json.dumps(packet,ensure_ascii=False,separators=(',',':'))
    return result


def retain_by_tokens(runtime,messages,max_chars):
    """Use existing exact budget before discarding a reusable prefix at12k chars."""
    if runtime.version<7 or sum(len(x['content']) for x in messages)>max_chars:return False
    import time
    from neuro_next.context_budget import fit_request
    from neuro_next.model_adapter import ModelAdapter
    receipt={}
    try:
        adapter=ModelAdapter();opts=runtime.store.specification(runtime.task)['options']
        model=opts['model_id'];profile=opts['model_profile']
        request={'model':model,'messages':messages,'max_tokens':profile['max_tokens'],
                 'chat_template_kwargs':{'enable_thinking':profile['thinking']}}
        fit_request(adapter.config['endpoint'],model,request,profile['context'],
                    deadline=time.monotonic()+10,receipt=receipt)
        retain=not receipt.get('omitted_messages',0)
    except Exception as exc:
        receipt['fallback_reason']=type(exc).__name__;retain=False
    runtime.store.event(runtime.task,'prefix_budget_check',dict(receipt,prefix_retained=retain))
    return retain


def project(messages,current_task,delivered_tasks):
    result=copy.deepcopy(messages)
    if len(result)<4 or result[-2].get('role')!='user':return result
    try:packet=json.loads(result[-2]['content'])
    except (ValueError,TypeError):return result
    if not isinstance(packet,dict):return result
    events=packet.get('results',[])
    selections={e['event']:e for e in events if e.get('kind')=='reference_selection'}
    allowed={'exit_code','reference','presentation_complete','intent_id'}
    for index,item in enumerate(events):
        if item.get('kind')!='observation' or item.get('task')==current_task or item.get('task') not in delivered_tasks:continue
        data=item.get('data',{})
        if set(data)-allowed or (type(data.get('exit_code')) is not int or data.get('exit_code')!=0) or data.get('presentation_complete') is not True:continue
        if item.get('stdout') or item.get('stderr'):continue
        ref=data.get('reference',{})
        if not isinstance(ref,dict):continue
        expected={'view_event','position','source_event','source','row_index','original',
                  'original_observed_at','read_wall_s','identity_scope','selection_event'}
        if set(ref)-expected or type(ref.get('selection_event')) is not int:continue
        selection=selections.get(ref.get('selection_event'))
        if selection and selection.get('status') not in {'SAME_OBJECT','OBSERVED_AGAIN'}:continue
        if not selection or selection.get('task')!=item.get('task'):continue
        key=str(ref.get('view_event'))+':'+str(ref.get('position'))
        if selection.get('ref')!=key:continue
        events[index]={'event':item['event'],'kind':'observation',
                       'represented_by_reference_selection':selection['event'],
                       'original_record_ref':key}
    result[-2]['content']=json.dumps(packet,ensure_ascii=False,separators=(',',':'))
    return result
