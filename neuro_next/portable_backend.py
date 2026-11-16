"""Portable adapter: user-selected local llama.cpp, never production research switching."""
from urllib.parse import quote
from pathlib import Path
import time
from .installation import load
from .inference_control import get_json,request_lock,wait_for_idle,remaining
class PortableModelAdapter:
 def __init__(self,**kwargs):
  self.settings=load();c=self.settings
  self.config={'endpoint':c['endpoint'],'default_agent_model':c['model_id'],'fallback_model':None,'benchmark_model':None,'profiles':{c['model_id']:c['profile']}}
 def profile(self,model):return dict(self.config['profiles'][model])
 def status(self):
  c=self.settings;out={'status':'NOT_READY','active_model':None,'default_agent_model':c['model_id'],'fallback_model':None,'loaded_models':[],'reason':None}
  try:
   route='?model='+quote(c['model_id'],safe='')+'&autoload=false'
   models=get_json(c['endpoint'],'/v1/models',timeout=5)['data']
   if c['model_id'] not in [m['id'] for m in models]:raise ValueError('Configured model ID is not advertised')
   props=get_json(c['endpoint'],'/props',timeout=5)
   if props.get('role')=='router':
    if props.get('max_instances')!=1 or props.get('models_autoload')is not False:raise ValueError('Router must use models-max=1, autoload disabled')
    resident=[m for m in get_json(c['endpoint'],'/models',timeout=5)['data'] if m.get('status',{}).get('value') in {'loaded','loading','sleeping'}]
    if len(resident)!=1 or resident[0]['id']!=c['model_id'] or resident[0]['status']['value']!='loaded':raise ValueError('Load configured model explicitly; adapter will not switch unrelated models')
   elif len(models)!=1:raise ValueError('Single-model standalone server required')
   slots=get_json(c['endpoint'],'/slots'+route,timeout=5)
   if len(slots)!=1 or slots[0].get('n_ctx')!=c['profile']['context']:raise ValueError('One slot with configured context required (--parallel1 --slots)')
   out.update(status='READY',active_model=c['model_id'],loaded_models=[c['model_id']],resident_models=[c['model_id']])
  except Exception as exc:out['reason']=str(exc)
  return out
 def ensure_ready(self,deadline=None,**kwargs):
  if kwargs.get('benchmark_model') or kwargs.get('emergency'):raise ValueError('Portable alpha does not silently switch models')
  value=self.status()
  if value['status']!='READY':raise RuntimeError('Inference not ready: '+str(value['reason'])+'; run neuroos doctor')
  with request_lock(timeout=remaining(deadline,15)):wait_for_idle(self.config['endpoint'],value['active_model'],timeout=remaining(deadline,15))
  return value
 def request_primary(self):return self.status()
