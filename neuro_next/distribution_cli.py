"""Private-user installation UX. No downloads, sudo, automatic uploads or global edits."""
import argparse,json,os,platform,shutil,subprocess,sys,tarfile,tempfile,time,uuid,io,sqlite3
from pathlib import Path
from . import installation as inst
VERSION='0.1.0-alpha.1'
def memory():return {k:int(v.split()[0]) for k,v in (l.split(':',1) for l in Path('/proc/meminfo').read_text().splitlines())}
def gguf(path):
 p=Path(path)
 with p.open('rb') as stream:h=stream.read(24)
 if len(h)!=24 or h[:4]!=b'GGUF' or int.from_bytes(h[4:8],'little') not in {2,3}:raise ValueError('Not a supported GGUF v2/v3 file')
 return {'bytes':p.stat().st_size,'gguf_version':int.from_bytes(h[4:8],'little')}
def ask(label,default=''):
 value=input(label+(f' [{default}]' if default!='' else '')+': ').strip();return value or str(default)
def setup():
 p=inst.config_path()
 if p.exists() and ask('Replace existing user configuration? yes/no','no')!='yes':return
 active=subprocess.run(['systemctl','--user','is-active','--quiet','neuroos-inference.service'],capture_output=True,timeout=5)
 if active.returncode==0:raise ValueError('Stop your personal neuroos-inference.service explicitly before reconfiguration')
 print('Local inference only. No model/binary download. Choose an existing GGUF; install one separately if absent.')
 model=Path(ask('Existing GGUF path')).expanduser().resolve();info=gguf(model)
 kind=ask('Backend: external or managed','external')
 binary=ask('llama-server executable (needed for managed backend)',shutil.which('llama-server') or '')
 if binary:binary=str(Path(binary).expanduser().resolve())
 endpoint=ask('Loopback endpoint','http://127.0.0.1:8081');model_id=ask('Model alias advertised by backend','neuroos-local')
 ctx=int(ask('Context tokens',8192 if memory()['MemTotal']>=14*1024**2 else 4096))
 tokens=int(ask('Maximum generated tokens',min(2048,ctx//4)))
 data=Path(ask('Private task/history directory',inst.state_root())).expanduser().resolve()
 if data.exists() and (data.stat().st_uid!=os.getuid() or data.stat().st_mode&0o077):raise ValueError('Choose a private owned data directory0700; no global chmod performed')
 data.mkdir(parents=True,exist_ok=True,mode=0o700)
 reserve=max(512,memory()['MemTotal']//1024//10);budget=max(512,memory()['MemAvailable']//1024-reserve)
 cap=int(ask('Managed backend memory hard limit MiB',budget))
 c={'version':1,'backend':kind,'endpoint':endpoint,'model_id':model_id,'model_path':str(model),'llama_server':binary,'data_dir':str(data),'memory_max_mib':cap,'threads':min(4,os.cpu_count() or 1),'profile':{'context':ctx,'max_tokens':tokens,'thinking':False,'request_timeout_s':900}}
 inst.save(c);print('Saved private user config. Model bytes:',info['bytes']);print('Run neuroos doctor; neuroos doctor --probe makes one real inference request.')
 if kind=='managed':
  print('Optional USER service only, no root and no linger. It stays loaded between tasks.')
  if ask('Install limits and start your user backend now? yes/no','no')=='yes':backend('start')
 print('SSH logout persistence requires a user manager and possibly linger. Nothing enabled automatically.')
def doctor(probe=False):
 checks=[]
 def add(code,ok,message,severity='error'):checks.append({'code':code,'ok':bool(ok),'message':message,'severity':severity})
 osinfo=Path('/etc/os-release').read_text();add('platform','ID=debian' in osinfo and ('VERSION_ID="13"' in osinfo or 'VERSION_ID=13' in osinfo) and platform.machine()=='x86_64','Supported target: Debian13 x86-64; other systems unqualified')
 for tool in ['python3','bash','systemctl','systemd-run','unshare','ps','df','ip','busctl']:
  add('tool_'+tool,shutil.which(tool),f'{tool}: install the corresponding Debian dependency')
 for tool in ['tmux','nmcli']:add('optional_'+tool,shutil.which(tool),f'{tool}: optional; plain chat/read-only sources remain available','warning')
 c=None
 try:c=inst.load();add('config',True,'Private configuration valid')
 except Exception as e:add('config',False,str(e))
 if c:
  try:info=gguf(c['model_path']);add('gguf',True,f'GGUF v{info["gguf_version"]}, {info["bytes"]}bytes; runtime loader must support architecture')
  except Exception as e:add('gguf',False,str(e))
  data=Path(c['data_dir']);add('data_permissions',data.is_dir() and data.stat().st_uid==os.getuid() and not data.stat().st_mode&0o077,'Data must be private0700 and owned by current user')
  if c['llama_server']:
   try:
    r=subprocess.run([c['llama_server'],'--version'],capture_output=True,text=True,timeout=10);add('llama_binary',r.returncode==0,'llama-server --version: '+(r.stdout+r.stderr)[:300])
   except Exception as e:add('llama_binary',False,str(e))
  else:add('llama_binary',c['backend']=='external','External backend binary not managed by package','warning')
  from .portable_backend import PortableModelAdapter
  status=PortableModelAdapter().status();add('endpoint',status['status']=='READY',status.get('reason') or 'Configured single model/context available')
  m=memory();add('memory',m['MemAvailable']>=max(256*1024,m['MemTotal']//10),'Host available RAM must exceed safety reserve; smaller model/context may be needed')
  if c['backend']=='external':add('external_limits',False,'External backend resource limits/weight identity remain operator responsibility','warning')
  try:
   from .user_systemd import UserSystemd
   UserSystemd.connect();add('user_manager',True,'Authenticated current UID user manager/bus available')
  except Exception as e:add('user_manager',False,'Log in through a normal local/SSH session: '+str(e))
  try:
   import ctypes
   libc=ctypes.CDLL(None,use_errno=True);abi=libc.syscall(444,0,0,1)
   add('landlock',abi>=3,'Landlock ABI>=3 and libseccomp2 required; no unsafe execution fallback')
   ctypes.CDLL('libseccomp.so.2')
  except Exception as e:add('landlock',False,str(e))
  try:
   from .execution_workspace import preflight
   value=preflight();add('rootless',value['pass'],'Rootless namespaces/Landlock: '+str(value))
  except ImportError:
   r=subprocess.run(['unshare','--user','--map-root-user','--mount','--pid','--fork','true'],capture_output=True,timeout=5);add('rootless',r.returncode==0,'User/mount/PID namespaces required; execution still fails closed on Landlock setup errors')
  except Exception as e:add('rootless',False,str(e))
  if probe and status['status']=='READY':
   try:
    from .inference_control import completion_request,request_lock,get_json
    schema={'type':'object','properties':{'ok':{'type':'boolean','enum':[True]}},'required':['ok'],'additionalProperties':False}
    request={'model':c['model_id'],'messages':[{'role':'user','content':'Return the JSON object with ok true.'}],'max_tokens':min(2048,c['profile']['max_tokens']),'temperature':0,'stream':False,'chat_template_kwargs':{'enable_thinking':c['profile']['thinking']},'response_format':{'type':'json_object','schema':schema}}
    raw=json.loads(completion_request(c['endpoint'],c['model_id'],request,c['profile']['request_timeout_s']))
    add('real_probe',raw['choices'][0]['finish_reason']=='stop' and json.loads(raw['choices'][0]['message']['content'])=={'ok':True},'Live configured-backend completion returned constrained JSON; backend provenance is operator-controlled')
   except Exception as e:add('real_probe',False,str(e))
 return checks
def backend(action):
 c=inst.load()
 if c['backend']!='managed':raise ValueError('External server is operator-managed; no start/stop performed')
 if action in {'start','stop','status'}:
  from .user_systemd import UserSystemd
  bus=UserSystemd.connect()
  if action=='start':
   directory=Path.home()/'.config/systemd/user/neuroos-inference.service.d';directory.mkdir(parents=True,exist_ok=True)
   (directory/'limits.conf').write_text('[Service]\nMemoryMax='+str(c['memory_max_mib'])+'M\nMemorySwapMax=0\n')
   subprocess.run(['systemctl','--user','daemon-reload'],env=bus.environment(),check=True)
  return subprocess.run(['systemctl','--user',action,'neuroos-inference.service'],env=bus.environment(),check=True).returncode
 if action!='serve':raise ValueError('Unknown backend operation')
 group=Path('/sys/fs/cgroup')/Path('/proc/self/cgroup').read_text().strip().split('::')[-1].lstrip('/')
 if 'neuroos-inference.service' not in str(group) or not (group/'memory.max').exists() or (group/'memory.max').read_text().strip()=='max' or int((group/'memory.max').read_text())>c['memory_max_mib']*1024**2 or (group/'memory.swap.max').read_text().strip()!='0':raise ValueError('Use neuroos backend start: bounded user service with MemoryMax and no swap is required')
 info=gguf(c['model_path']);mem=memory();reserve=max(512*1024,mem['MemTotal']//10);estimated=info['bytes']+max(512*1024**2,c['profile']['context']*131072)+info['bytes']//5
 if estimated>c['memory_max_mib']*1024**2 or estimated>max(0,mem['MemAvailable']-reserve)*1024:raise ValueError('Fresh memory admission denied. Choose smaller GGUF/context; limits are not increased automatically. Estimate includes file,20%headroom and conservative KV allowance, not a measured peak.')
 from urllib.parse import urlsplit
 u=urlsplit(c['endpoint']);argv=[c['llama_server'],'--model',c['model_path'],'--alias',c['model_id'],'--host',u.hostname,'--port',str(u.port or 80),'--ctx-size',str(c['profile']['context']),'--parallel','1','--slots','--n-gpu-layers','0','--threads',str(c['threads']),'--batch-size','128','--ubatch-size','64']
 os.execv(c['llama_server'],argv)
def feedback(output=None,task=None):
 # Allowlist only. No doctor messages, paths, SSIDs, prompts, argv, stdout, env.
 report={'version':VERSION,'architecture':platform.machine(),'python':platform.python_version(),'os_release_id':next((l.split('=',1)[1].strip('"') for l in Path('/etc/os-release').read_text().splitlines() if l.startswith('VERSION_ID=')),None),'tools':{t:bool(shutil.which(t)) for t in ['systemctl','unshare','tmux','nmcli']}}
 try:
  c=inst.load();report['configuration']={'backend':c['backend'],'profile':c['profile'],'memory_max_mib':c['memory_max_mib']}
 except Exception:report['configuration']='not_configured_or_invalid'
 members={'report.json':json.dumps(report,indent=2).encode()}
 if task:
  import re
  if not re.fullmatch('[0-9a-f]{16}',task):raise ValueError('Invalid task ID')
  c=inst.load();db=sqlite3.connect('file:'+str(Path(c['data_dir'])/'state.sqlite')+'?mode=ro',uri=True);rows=[]
  for kind,data in db.execute('SELECT kind,data FROM events WHERE task=? ORDER BY id',(task,)):
   d=json.loads(data);safe_kinds={'model','intent','observation','controller_feedback','verification_failed','external_verification','reference_selection','presentation_view','completion_state','submitted','inference_started','reference_clarification'};r={'kind':kind if kind in safe_kinds else 'other'}
   if kind=='model':r['usage']={k:v for k,v in (d.get('response') or {}).get('usage',{}).items() if k in {'prompt_tokens','completion_tokens','total_tokens'} and type(v)is int};r['duration_s']=d.get('duration_s') if type(d.get('duration_s')) in {int,float} and __import__('math').isfinite(d['duration_s']) else None
   rows.append(r)
  db.close();members['trace-metadata.json']=json.dumps(rows).encode()
 try:directory=inst.state_root()/'feedback'
 except (ValueError,OSError):directory=Path.home()/'.local/state/neuroos/feedback'
 directory.mkdir(parents=True,exist_ok=True,mode=0o700);path=Path(output) if output else directory/('neuroos-feedback-'+uuid.uuid4().hex[:12]+'.tar.gz')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f,tarfile.open(fileobj=f,mode='w:gz') as tar:
  for name,body in members.items():item=tarfile.TarInfo(name);item.size=len(body);tar.addfile(item,io.BytesIO(body))
 print('Created locally; inspect before sharing. No upload:',path)
def main():
 args=sys.argv[1:]
 if args in [['--version'],['version']]:print(VERSION);return
 if os.geteuid()==0:raise SystemExit('Run NeuroOS as an ordinary user; sudo is only for apt install/remove.')
 p=argparse.ArgumentParser(prog='neuroos');sub=p.add_subparsers(dest='cmd')
 sub.add_parser('setup');d=sub.add_parser('doctor');d.add_argument('--probe',action='store_true');d.add_argument('--json',action='store_true')
 f=sub.add_parser('feedback');f.add_argument('--output');f.add_argument('--include-traces',metavar='TASK_ID',help='Explicitly include sanitized event/timing metadata only, never raw contents')
 be=sub.add_parser('backend');be.add_argument('action',choices=['start','stop','status','serve'])
 if not args or args[0] in {'setup','doctor','feedback','backend','--help','-h'}:
  a=p.parse_args(args)
  try:
   if a.cmd=='setup':setup()
   elif a.cmd=='doctor':
    checks=doctor(a.probe);print(json.dumps(checks,indent=2) if a.json else '\n'.join(('OK' if x['ok'] else 'WARN' if x['severity']=='warning' else 'FAIL')+' '+x['code']+': '+x['message'] for x in checks));raise SystemExit(0 if all(x['ok'] or x['severity']=='warning' for x in checks) else 1)
   elif a.cmd=='feedback':feedback(a.output,a.include_traces)
   elif a.cmd=='backend':backend(a.action)
   else:p.print_help()
  except (ValueError,OSError,RuntimeError,sqlite3.Error,subprocess.SubprocessError) as e:raise SystemExit(str(e))
  return
 try:c=inst.load()
 except (ValueError,OSError) as e:raise SystemExit(str(e))
 os.environ['NEUROOS_CONFIG']=str(inst.config_path().resolve())
 inst.prepare_data()
 from . import persistent
 sys.argv=[sys.argv[0],'--state',c['data_dir'],*args];persistent.main()
if __name__=='__main__':main()
