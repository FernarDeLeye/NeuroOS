"""Validated host access to the current approved user's systemd manager.

Does not start managers, enable linger, change policy or trust session addresses.
Qualified transport: Debian's systemd-owned, socket-activated user bus.
"""
import json,os,pwd,socket,stat,struct,subprocess
from pathlib import Path

class UserBusError(RuntimeError):
    def __init__(self,phase,message,**evidence):
        self.evidence={'phase':phase,'message':message,**evidence}
        super().__init__(json.dumps(self.evidence,ensure_ascii=False))

def clean_environment():
    return {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
            'SYSTEMD_PAGER':'cat','SYSTEMD_COLORS':'0','PAGER':'cat'}

def validate_node(path,uid,kind,private=False):
    try:info=Path(path).lstat()
    except OSError as exc:raise UserBusError('filesystem','required runtime node unavailable',path=str(path),error=str(exc)) from exc
    valid=stat.S_ISDIR(info.st_mode) if kind=='directory' else stat.S_ISSOCK(info.st_mode)
    if not valid or info.st_uid!=uid or info.st_mode&0o022 or (private and info.st_mode&0o077):
        # The bus socket may be0666; its private parent and authenticated peer
        # are the security boundary, not a world-writable socket mode alone.
        if not (kind=='socket' and valid and info.st_uid==uid):
            raise UserBusError('filesystem','unsafe runtime node',path=str(path),uid=info.st_uid,mode=oct(stat.S_IMODE(info.st_mode)),expected_uid=uid,kind=kind)
    return (info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode))

def checked(argv,env,phase,timeout=5):
    try:r=subprocess.run(argv,capture_output=True,text=True,env=env,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as exc:raise UserBusError(phase,'command unavailable or timed out',argv=argv,error=str(exc)) from exc
    if r.returncode:raise UserBusError(phase,'command failed',argv=argv,returncode=r.returncode,stdout=r.stdout,stderr=r.stderr)
    return r

def manager_properties(uid):
    r=checked(['/usr/bin/systemctl','--system','--no-pager','show',f'user@{uid}.service',
        '-p','ActiveState','-p','SubState','-p','MainPID','-p','ControlGroup'],clean_environment(),'system_manager')
    props=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
    try:pid=int(props.get('MainPID','0'))
    except ValueError:pid=0
    if props.get('ActiveState')!='active' or props.get('SubState')!='running' or pid<=0:
        raise UserBusError('manager','user manager is not active; no automatic start or linger',uid=uid,properties=props)
    return props

def process_identity(pid,uid):
    try:
        proc=Path('/proc')/str(pid);info=proc.stat();raw=(proc/'stat').read_text();start=raw.rsplit(') ',1)[1].split()[19]
        group=(proc/'cgroup').read_text()
    except (OSError,IndexError) as exc:raise UserBusError('manager_process','manager process identity unavailable',pid=pid,error=str(exc)) from exc
    if info.st_uid!=uid:raise UserBusError('manager_process','manager UID mismatch',pid=pid,uid=info.st_uid,expected_uid=uid)
    return {'pid':pid,'uid':uid,'starttime':start,'cgroup':group}

class UserSystemd:
    @classmethod
    def connect(cls,user=None):
        uid=os.getuid()
        if user is None:user=pwd.getpwuid(uid).pw_name
        try:account=pwd.getpwnam(user)
        except KeyError as exc:raise UserBusError('identity','approved account does not exist',user=user) from exc
        if uid==0 or os.geteuid()!=uid or account.pw_uid!=uid:
            raise UserBusError('identity','requires real/effective UID of approved non-root account',user=user,uid=uid,euid=os.geteuid(),expected_uid=account.pw_uid)
        obj=cls();obj.uid=uid;obj.user=user;obj.runtime=Path('/run/user')/str(uid);obj.bus=obj.runtime/'bus'
        validate_node('/run',0,'directory');validate_node('/run/user',0,'directory')
        obj.runtime_identity=validate_node(obj.runtime,uid,'directory',private=True)
        obj.bus_identity=validate_node(obj.bus,uid,'socket')
        obj.manager=manager_properties(uid);pid=int(obj.manager['MainPID']);obj.process=process_identity(pid,uid)
        cg=obj.manager.get('ControlGroup','')
        actual=[line.split(':',2)[-1] for line in obj.process['cgroup'].splitlines()]
        if not cg or not any(g==cg or g.startswith(cg+'/') for g in actual):raise UserBusError('manager_process','manager cgroup mismatch',expected=cg,actual=actual)
        obj.address='unix:path='+str(obj.bus)
        obj._peer()
        owner=checked(['/usr/bin/busctl','--address='+obj.address,'--auto-start=no','--json=short','call','org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetConnectionUnixProcessID','s','org.freedesktop.systemd1'],clean_environment(),'bus_owner')
        try:owner_pid=json.loads(owner.stdout)['data'][0]
        except (ValueError,KeyError,IndexError,TypeError) as exc:raise UserBusError('bus_owner','invalid owner credentials',stdout=owner.stdout) from exc
        if type(owner_pid)is not int or owner_pid!=pid:raise UserBusError('bus_owner','bus systemd owner differs from independently discovered manager',owner_pid=owner_pid,manager_pid=pid)
        obj.check();return obj

    def _peer(self):
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                sock.settimeout(2);sock.connect(str(self.bus))
                pid,uid,gid=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        except OSError as exc:raise UserBusError('socket','user bus is unavailable',path=str(self.bus),error=str(exc)) from exc
        if uid!=self.uid or pid!=int(self.manager['MainPID']):
            raise UserBusError('socket','unqualified bus peer; expected systemd socket owner',peer_pid=pid,peer_uid=uid,expected_pid=int(self.manager['MainPID']),expected_uid=self.uid)
        return {'pid':pid,'uid':uid,'gid':gid}

    def check(self):
        if os.getuid()!=self.uid or os.geteuid()!=self.uid:raise UserBusError('identity','caller UID changed')
        if validate_node(self.runtime,self.uid,'directory',private=True)!=self.runtime_identity or validate_node(self.bus,self.uid,'socket')!=self.bus_identity:
            raise UserBusError('filesystem','runtime/bus identity changed; reconnect required')
        if manager_properties(self.uid)!=self.manager or process_identity(int(self.manager['MainPID']),self.uid)!=self.process:
            raise UserBusError('manager','manager identity/state changed; reconnect required')
        self._peer()

    def environment(self):
        self.check();return self._child_environment()

    def _child_environment(self):
        return clean_environment()|{'XDG_RUNTIME_DIR':str(self.runtime),'DBUS_SESSION_BUS_ADDRESS':self.address}

    def systemctl(self,args,timeout=5):
        if not args or args[0] not in {'list-units','show','is-active','start','kill','stop','reset-failed'} or any(str(a).startswith(('--system','--host','--machine','--root','--image','--user','-H','-M')) for a in args):
            raise UserBusError('arguments','only local user-manager operations are permitted')
        self.check();r=checked(['/usr/bin/systemctl','--user','--no-pager',*args],self._child_environment(),'user_operation',timeout)
        self.check();return r

    def receipt(self):
        return {'uid':self.uid,'user':self.user,'runtime':str(self.runtime),'address':self.address,
                'manager':self.manager,'process':self.process,'peer':self._peer(),'transport':'validated systemd-owned user socket'}
