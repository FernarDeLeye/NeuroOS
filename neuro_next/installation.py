"""Installed-layout configuration; the research checkout remains opt-out."""
import json,os,ipaddress,stat
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
def installed():return (ROOT/'INSTALLATION').is_file()
def config_path():return Path(os.environ.get('NEUROOS_CONFIG',str(Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'neuroos/config.json')))
def state_root():
 if config_path().exists():return Path(load()['data_dir'])
 return Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'neuroos'
def load():
 p=config_path()
 if not p.exists():raise ValueError('NeuroOS is not configured. Run: neuroos setup')
 info=p.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:raise ValueError('Configuration must be an owned private regular file (0600)')
 c=json.loads(p.read_text());validate(c);return c
def validate(c):
 if not isinstance(c,dict) or not {'version','backend','endpoint','model_id','model_path','llama_server','data_dir','memory_max_mib','threads','profile'}<=set(c):raise ValueError('Incomplete configuration; rerun neuroos setup')
 if c.get('version')!=1:raise ValueError('Unsupported config version')
 u=urlsplit(c['endpoint'])
 if u.scheme!='http' or not u.hostname or not ipaddress.ip_address(u.hostname).is_loopback or u.username or u.password or u.query or u.fragment or u.path not in {'','/'}:raise ValueError('Use an explicit loopback HTTP endpoint, no credentials/path')
 if not isinstance(c['model_id'],str) or not c['model_id'] or len(c['model_id'])>128:raise ValueError('Invalid backend model ID')
 p=c['profile']
 if not isinstance(p,dict) or set(p)!={'context','max_tokens','thinking','request_timeout_s'}:raise ValueError('Invalid inference profile fields')
 if type(p['context'])is not int or not 4096<=p['context']<=32768:raise ValueError('Context must be4096..32768')
 if type(p['max_tokens'])is not int or not 512<p['max_tokens']<=p['context']//2:raise ValueError('Output budget must be513..context/2')
 if type(p['thinking'])is not bool or not 30<=p['request_timeout_s']<=1200:raise ValueError('Invalid profile bounds')
 if type(c.get('threads'))is not int or not 1<=c['threads']<=64:raise ValueError('Thread count must be1..64')
 if not isinstance(c.get('llama_server'),str):raise ValueError('Invalid binary path')
 if c['backend']=='managed' and (not Path(c['llama_server']).is_absolute() or not os.access(c['llama_server'],os.X_OK)):raise ValueError('Choose an existing executable llama-server')
 if c['backend'] not in {'external','managed'}:raise ValueError('Unsupported backend')
 for name in ['model_path','data_dir']:
  if not Path(c[name]).is_absolute() or any(x in c[name] for x in '\x00\n\r'):raise ValueError('Use an absolute clean '+name)
 if type(c['memory_max_mib'])is not int or c['memory_max_mib']<512:raise ValueError('Memory limit must be at least512MiB')
 return c
def save(c):
 validate(c);p=config_path();p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 info=p.parent.lstat()
 if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:raise ValueError('Unsafe config directory')
 from .durable import save as atomic
 atomic(p,c);p.chmod(0o600)

def prepare_data():
 """Installed-only additive schema migration, consistent backup before legacy import."""
 import sqlite3,time
 from .store import Store
 from .chat import ChatStore
 root=state_root();root.mkdir(parents=True,exist_ok=True,mode=0o700)
 info=root.stat()
 if info.st_uid!=os.getuid() or info.st_mode&0o077:raise ValueError('Private data directory must be owned0700')
 path=root/'state.sqlite'
 if path.exists():
  with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as old:
   version=old.execute('PRAGMA user_version').fetchone()[0]
   if version>1:raise ValueError('Database is newer than this alpha. Do not downgrade without a compatible backup.')
   if version==0:
    backups=root/'backups';backups.mkdir(exist_ok=True,mode=0o700);backup=backups/('pre-alpha-schema-'+str(time.time_ns())+'.sqlite')
    with sqlite3.connect(backup) as target:old.backup(target)
    backup.chmod(0o600)
 st=Store(root)
 try:ChatStore(st);st.db.execute('PRAGMA user_version=1');st.db.commit()
 finally:st.db.close()
