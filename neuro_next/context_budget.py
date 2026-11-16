"""Exact local chat-template/token budget; no inference or model switching."""
import copy,json
from urllib.parse import quote
from .inference_control import get_json,request_lock,wait_for_idle,remaining,InferenceCancelled
from .dialogue_context import DialogueContextLimit

def fit_request(endpoint,model,request,context_size,deadline=None,cancel_event=None,receipt=None):
    if type(context_size)is not int or context_size<=0:raise ValueError('finite context required')
    output=request['max_tokens'];margin=32
    if type(output)is not int or not 0<output<context_size-margin:raise ValueError('invalid output reservation')
    result=copy.deepcopy(request);log=receipt if receipt is not None else {};log.update(context_size=context_size,output_reservation=output,margin_tokens=margin,measurements=[],omitted_messages=0)
    route='?model='+quote(model,safe='')+'&autoload=false'
    with request_lock(timeout=remaining(deadline,10)):
        wait_for_idle(endpoint,model,timeout=remaining(deadline,10))
        while True:
            if cancel_event is not None and cancel_event.is_set():raise InferenceCancelled('context budget cancelled')
            rendered=get_json(endpoint,'/apply-template'+route,timeout=remaining(deadline,10),body=result)
            if not isinstance(rendered.get('prompt'),str) or not rendered['prompt']:raise RuntimeError('chat template metadata invalid')
            tokenized=get_json(endpoint,'/tokenize'+route,timeout=remaining(deadline,10),body={'model':model,'content':rendered['prompt'],'add_special':True,'parse_special':True})
            tokens=tokenized.get('tokens')
            if not isinstance(tokens,list) or not tokens or any(type(t)is not int for t in tokens):raise RuntimeError('token metadata invalid')
            count=len(tokens);log['measurements'].append({'prompt_tokens':count,'message_count':len(result['messages'])})
            if count+output+margin<=context_size:
                log['prompt_tokens']=count;log['fits']=True;return result
            if len(result['messages'])<=4:
                log['fits']=False;raise DialogueContextLimit('token context budget exhausted; mandatory goal/latest observation retained, inference not sent')
            del result['messages'][2:4];log['omitted_messages']+=2
            last=result['messages'][-1]
            if last['role']!='user':raise RuntimeError('dialogue must end in host observation')
            try:state=json.loads(last['content'])
            except ValueError:raise RuntimeError('host dialogue state must be JSON')
            state['older_dialogue_messages_omitted_for_tokens']=log['omitted_messages'];last['content']=json.dumps(state,ensure_ascii=False)
