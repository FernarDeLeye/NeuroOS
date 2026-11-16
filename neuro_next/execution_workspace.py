"""Rootless filesystem/process isolation and persistent, unpublished scratch.

Requires user namespaces, user-systemd resource scopes and Landlock. There is no
plain-shell fallback. Host live data is never writable by the command.
"""
import ctypes,hashlib,json,os,selectors,shutil,signal,subprocess,time,uuid
from pathlib import Path
from .workspace_tree import logical_path,snapshot,materialize,metadata


def remove_private_tree(path):
    # All workers are dead; only a private control-owned generation is removed.
    # Never chmod or follow model-created symlinks.
    os.chmod(path,0o700,follow_symlinks=False)
    for root,dirs,_ in os.walk(path,followlinks=False):
        for name in dirs:
            p=Path(root)/name
            if not p.is_symlink():os.chmod(p,0o700,follow_symlinks=False)
    shutil.rmtree(path)

def run_bounded(argv,env,stdin,timeout,limit=262144,*,logs=None,cancel_event=None,input_fifo=None):
    process=subprocess.Popen(argv,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
    logfiles={k:open(p,'ab',buffering=0) for k,p in (logs or {}).items()}
    selector=selectors.DefaultSelector();streams={'stdout':bytearray(),'stderr':bytearray()};data=bytearray(stdin.encode());offset=0;started=time.monotonic();timed_out=limited=cancelled=False
    def kill():
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    for pipe,key in [(process.stdout,'stdout'),(process.stderr,'stderr')]:os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,key)
    fifo=None;messages=bytearray();close_input=input_fifo is None;received=len(data)
    if input_fifo is not None:
        os.mkfifo(input_fifo,0o600);fifo=os.open(input_fifo,os.O_RDWR|os.O_NONBLOCK);selector.register(fifo,selectors.EVENT_READ,'input_fifo')
    os.set_blocking(process.stdin.fileno(),False)
    if data:selector.register(process.stdin,selectors.EVENT_WRITE,'stdin')
    elif close_input:process.stdin.close()
    try:
        while selector.get_map():
            if time.monotonic()-started>timeout:timed_out=True;kill()
            if cancel_event is not None and cancel_event.is_set():cancelled=True;kill()
            if process.poll() is not None:
                kill()
                if fifo is not None:selector.unregister(fifo);os.close(fifo);fifo=None
                if not process.stdin.closed:
                    try:selector.unregister(process.stdin)
                    except KeyError:pass
                    process.stdin.close()
            if input_fifo is not None and not process.stdin.closed:
                registered=any(k.data=='stdin' for k in selector.get_map().values())
                if offset<len(data) and not registered:selector.register(process.stdin,selectors.EVENT_WRITE,'stdin')
                if offset==len(data) and close_input:
                    if registered:selector.unregister(process.stdin)
                    process.stdin.close()
            for key,_ in selector.select(.05):
                pipe,name=key.fileobj,key.data
                if name=='input_fifo':
                    messages.extend(os.read(fifo,4096))
                    if len(messages)>8192:limited=True;kill();messages.clear();continue
                    while b'\n' in messages:
                        line,_,rest=messages.partition(b'\n');messages=bytearray(rest);message=json.loads(line)
                        if message.get('eof'):close_input=True
                        elif not close_input:
                            part=message['text'].encode();received+=len(part)
                            if received>65536:limited=True;kill();break
                            data.extend(part)
                    if close_input:
                        selector.unregister(fifo);os.close(fifo);fifo=None
                    continue
                if name=='stdin':
                    if pipe.closed:continue
                    try:offset+=os.write(pipe.fileno(),data[offset:offset+4096])
                    except BrokenPipeError:offset=len(data)
                    if offset==len(data):
                        selector.unregister(pipe)
                        if close_input:pipe.close()
                else:
                    part=os.read(pipe.fileno(),8192)
                    if not part:selector.unregister(pipe);pipe.close();continue
                    available=limit-sum(map(len,streams.values()));piece=part[:max(0,available)];streams[name].extend(piece)
                    if name in logfiles:logfiles[name].write(piece);os.fsync(logfiles[name].fileno())
                    if len(part)>available:limited=True;kill()
        process.wait(timeout=2)
    finally:
        kill();process.wait(timeout=2);selector.close()
        for pipe in [process.stdin,process.stdout,process.stderr]:
            if not pipe.closed:pipe.close()
        for log in logfiles.values():log.close()
        if fifo is not None:os.close(fifo)
        if input_fifo is not None:
            try:os.unlink(input_fifo)
            except FileNotFoundError:pass
    return {'exit_code':process.returncode,**{k:v.decode(errors='replace') for k,v in streams.items()},'timed_out':timed_out,'cancelled':cancelled,'output_limited':limited,'duration_s':time.monotonic()-started}

class ExecutionWorkspace:
    def __init__(self,root,live):
        if os.geteuid()==0:raise RuntimeError('host execution must be rootless')
        self.root=Path(root).resolve();self.live=Path(live).resolve()
        if self.root.is_relative_to(self.live):raise ValueError('control/scratch root must be outside live publication tree')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.root.stat().st_uid!=os.getuid() or self.root.stat().st_mode&0o077:raise ValueError('private owned control directory required')
        self.scratch=self.root/'scratch'
        self.host_namespaces={name:os.readlink('/proc/self/ns/'+name) for name in ['mnt','pid','net','user']}
        if not self.scratch.exists():
            self.scratch.mkdir(mode=0o700);materialize(snapshot(self.live),self.scratch)
    def tree(self):return snapshot(self.scratch)
    def cwds(self):return ['.','/work']+sorted(alias for p,v in self.tree().items() if v['kind']=='dir' for alias in [p,'/work/'+p])
    def exchange(self,export):
        from .durable import sync_dir
        libc=ctypes.CDLL(None,use_errno=True)
        if libc.renameat2(-100,os.fsencode(self.scratch),-100,os.fsencode(export),2)!=0:raise OSError(ctypes.get_errno(),'atomic scratch exchange failed')
        sync_dir(self.root)
    def execute(self,action,timeout=10,*,operation_id=None,cancel_event=None,pty_mode=False,persistent_stdin=False):
        try:
            from .reliability import strict_action
            # Existing generic shell validation validates env/length; virtual
            # absolute /work paths are normalized before invoking that boundary.
            cwd=action.get('cwd','.');relative=logical_path(cwd,root=True)
            strict_action({'kind':'shell',**{k:v for k,v in action.items() if k!='kind'},'cwd':relative or '.'})
            if cwd not in self.cwds():raise ValueError('cwd must be an advertised directory; symlink cwd is not allowed')
        except (ValueError,TypeError) as exc:
            return {'exit_code':126,'stdout':'','stderr':str(exc),'timed_out':False,'output_limited':False,'error':{'category':'path_policy','message':str(exc),'allowed_cwds':self.cwds()}}
        token=uuid.uuid4().hex;export=self.root/('export-'+token);mountroot=self.root/('root-'+token);report=self.root/('report-'+token+'.json')
        export.mkdir(mode=0o700);mountroot.mkdir(mode=0o700)
        unit='neuroos-workspace-'+token+'.scope'
        argv=['/usr/bin/systemd-run','--user','--scope','--quiet','--unit='+unit,'-p','MemoryMax=384M','-p','MemorySwapMax=0','-p','TasksMax=32','-p','CPUQuota=100%','-p','RuntimeMaxSec='+str(int(timeout)+6)+'s','-p','TimeoutStopSec=2s',
          '/usr/bin/unshare','--user','--map-root-user','--mount','--pid','--fork','--kill-child','--net','--ipc','--uts','--propagation','private',
          '/usr/bin/python3',str(Path(__file__).with_name('workspace_worker.py')),str(self.scratch),str(export),str(mountroot),str(report)]
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','XDG_RUNTIME_DIR':'/run/user/'+str(os.getuid()),
             'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/'+str(os.getuid())+'/bus',
             'NEURO_WORKSPACE_ACTION':json.dumps(action)}
        before=metadata(self.tree());journal=None
        if operation_id is not None:
            from .operation_journal import OperationJournal,digest
            journal=OperationJournal(self,operation_id)
            if journal.path.exists():raise RuntimeError('operation already recorded; reconcile, never replay')
            journal.write(phase='RUNNING',token=token,unit=unit,action=action,before=before,before_sha256=digest(self.tree()),timeout=timeout,pty=pty_mode,stdin_channel=persistent_stdin)
        env['NEURO_WORKSPACE_CPU_SECONDS']=str(max(20,min(601,int(timeout)+1)))
        try:
            if pty_mode:
                if journal is None:raise ValueError('PTY requires persistent operation ownership')
                from .pty_io import run as run_pty
                result=run_pty(argv,env,action.get('stdin',''),timeout,journal.logs(),self.root/('operation-'+str(operation_id)+'.stdin'),cancel_event)
            else:result=run_bounded(argv,env,action.get('stdin',''),timeout,logs=journal.logs() if journal else None,cancel_event=cancel_event,input_fifo=self.root/('operation-'+str(operation_id)+'.stdin') if persistent_stdin and journal else None)
            if report.exists() and report.stat().st_size and not result['timed_out'] and not result['output_limited'] and not result.get('cancelled'):
                proof=json.loads(report.read_text());after=snapshot(export)
                if proof.get('export_complete') is not True:raise RuntimeError('unconfirmed scratch export')
                if journal:
                    from .operation_journal import digest
                    journal.write(phase='IMPORTING',after_sha256=digest(after),result=result)
                self.exchange(export)
                # export now holds the previous generation; scratch was never absent.
                remove_private_tree(export)
                current=metadata(after);result['isolation']=proof['isolation'];result['changes']={p:{'before':before.get(p),'after':current.get(p)} for p in sorted(before.keys()|current.keys()) if before.get(p)!=current.get(p)}
                if result['exit_code']==125 and not proof['isolation'].get('child_setup_complete'):result['error']={'category':'isolation_setup','message':result['stderr']}
                elif 'oom_kill 0' not in proof['isolation']['memory_events']:result['error']={'category':'resource_limit','message':'Task cgroup OOM kill'}
                elif result['exit_code']!=0:result['error']={'category':'command_failure','message':'Command exited nonzero; scratch observations retained.'}
            else:
                result['error']={'category':'cancelled' if result.get('cancelled') else 'resource_limit' if result['timed_out'] or result['output_limited'] else 'isolation_or_export','message':result['stderr'] or 'No trusted supervisor report; scratch not imported.'}
                if not result['timed_out'] and not result.get('cancelled'):result['exit_code']=125
                if result.get('cancelled'):result['wrapper_exit_code']=result['exit_code'];result['exit_code']=None;result['exit_status_known']=False
            result['scope']=unit
            if journal:journal.write(phase='IMPORTED' if 'isolation' in result else 'FAILED',result=result)
            return result
        finally:
            # PID namespace init death kills all namespace descendants. This
            # additional user-scope kill also handles wrapper/setup failures.
            subprocess.run(['/usr/bin/systemctl','--user','kill','--signal=KILL','--kill-whom=all',unit],env=env,capture_output=True,timeout=5)
            if export.exists():remove_private_tree(export)
            # Mounts existed solely in the now-dead private namespace.
            if mountroot.exists():mountroot.rmdir()
