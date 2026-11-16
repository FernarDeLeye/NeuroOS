"""Read-only host available-memory/OOM guard; backend cgroup limits are external."""
import json,os,threading,time
from pathlib import Path
class Resources:
 def __init__(self,output,cancel_event,model=None):
  self.output=Path(output);self.cancel_event=cancel_event;self.reason=None;self.samples=[];self.stop=threading.Event();self.thread=threading.Thread(target=self.sample,daemon=True)
 def sample(self):
  baseline=None
  with (self.output/'resources.jsonl').open('a') as out:
   while not self.stop.is_set():
    try:
     mem={k:int(v.split()[0]) for k,v in (l.split(':',1) for l in Path('/proc/meminfo').read_text().splitlines())};vm=dict(l.split() for l in Path('/proc/vmstat').read_text().splitlines());oom=int(vm['oom_kill']);total=mem['MemTotal'];reserve=max(256*1024,min(1024*1024,total//10))
     row={'time':time.time(),'mem_available_kib':mem['MemAvailable'],'reserve_kib':reserve,'oom_kill':oom}
     if baseline is None:baseline=oom
     if mem['MemAvailable']<reserve:self.reason='Host available memory below reserved safety budget'
     if oom>baseline:self.reason='New host OOM event'
     if self.reason:self.cancel_event.set()
     self.samples.append(row);out.write(json.dumps(row)+'\n');out.flush()
    except Exception as exc:self.reason='Resource telemetry unavailable: '+str(exc);self.cancel_event.set()
    self.stop.wait(.5)
 def __enter__(self):self.thread.start();return self
 def __exit__(self,*args):self.stop.set();self.thread.join(timeout=3)
