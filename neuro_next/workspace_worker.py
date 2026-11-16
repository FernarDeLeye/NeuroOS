"""Trusted PID-1 supervisor inside rootless user/mount/PID/net/IPC namespaces.

No host helper/service, no persistent mount, no shell fallback. Parent holds
private export/report fds; untrusted child receives neither and sees no /proc.
"""
import ctypes as C,gc,json,os,resource,signal,subprocess,sys
from pathlib import Path
from workspace_tree import snapshot,materialize
from sandbox import Rule

libc=C.CDLL(None,use_errno=True)
def checked(value):
    if value<0:raise OSError(C.get_errno(),os.strerror(C.get_errno()))
    return value

def mount(source,target,kind=None,flags=0,options=None):
    checked(libc.mount(source.encode() if source else None,str(target).encode(),kind.encode() if kind else None,C.c_ulong(flags),options.encode() if options else None))

def bind_ro(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    if Path(source).is_dir():target.mkdir(exist_ok=True)
    else:target.touch()
    mount(source,target,flags=4096)
    mount(None,target,flags=4096|32|1|2|4) # remount bind readonly,nosuid,nodev

def drop_caps():
    checked(libc.prctl(38,1,0,0,0))
    for cap in range(41):
        if libc.prctl(24,cap,0,0,0)<0 and C.get_errno()!=22:raise OSError(C.get_errno(),'cap bounding drop')
    class Header(C.Structure):_fields_=[('version',C.c_uint32),('pid',C.c_int)]
    class Data(C.Structure):_fields_=[('effective',C.c_uint32),('permitted',C.c_uint32),('inheritable',C.c_uint32)]
    header=Header(0x20080522,0);data=(Data*2)();checked(libc.capset(C.byref(header),C.byref(data)))
    checked(libc.prctl(4,0,0,0,0)) # non-dumpable, no parent-fd introspection
    checked(libc.capget(C.byref(header),C.byref(data)))
    if any(d.effective or d.permitted or d.inheritable for d in data):raise RuntimeError('capabilities were not cleared')
    return 0

def child_filter(cpu_seconds=20):
    # True filesystem hiding is already enforced by mount namespace/chroot.
    # Landlock additionally confines accidental path exposure; all kernel
    # features enabling escape or host interaction remain denied by seccomp.
    libc.syscall.restype=C.c_long;abi=checked(libc.syscall(444,0,0,1))
    if abi<3:raise RuntimeError('Landlock ABI >=3 required')
    handled=(1<<(16 if abi>=5 else 15))-1;attr=C.c_uint64(handled)
    ruleset=checked(libc.syscall(444,C.byref(attr),C.sizeof(attr),0))
    try:
        for path,rights in [('/work',handled&~((1<<6)|(1<<9)|(1<<11)|(1<<15))),('/usr',(1<<0)|(1<<2)|(1<<3)),('/dev/null',(1<<1)|(1<<2)),('/dev/urandom',1<<2)]:
            fd=os.open(path,os.O_PATH)
            try:rule=Rule(rights,fd);checked(libc.syscall(445,ruleset,1,C.byref(rule),0))
            finally:os.close(fd)
        checked(libc.syscall(446,ruleset,0))
    finally:os.close(ruleset)
    sec=C.CDLL('libseccomp.so.2');sec.seccomp_init.argtypes=[C.c_uint32];sec.seccomp_init.restype=C.c_void_p
    sec.seccomp_rule_add.argtypes=[C.c_void_p,C.c_uint32,C.c_int,C.c_uint];sec.seccomp_syscall_resolve_name.argtypes=[C.c_char_p];sec.seccomp_load.argtypes=[C.c_void_p];sec.seccomp_release.argtypes=[C.c_void_p]
    ctx=sec.seccomp_init(0x7fff0000)
    if not ctx:raise RuntimeError('seccomp init failed')
    try:
        denied='''socket socketpair connect bind listen accept accept4 sendto sendmsg sendmmsg recvmsg recvmmsg
        io_uring_setup io_uring_enter io_uring_register ptrace process_vm_readv process_vm_writev
        pidfd_open pidfd_getfd pidfd_send_signal kill tkill tgkill rt_sigqueueinfo rt_tgsigqueueinfo
        mount umount2 pivot_root chroot unshare setns bpf perf_event_open userfaultfd reboot
        kexec_load kexec_file_load init_module finit_module delete_module open_by_handle_at name_to_handle_at
        keyctl add_key request_key mknod mknodat setsid setpgid mount_setattr move_mount fsopen fsconfig
        fsmount open_tree acct swapon swapoff quotactl settimeofday clock_settime
        shmget shmat shmdt shmctl msgget msgsnd msgrcv msgctl semget semop semtimedop semctl
        setpriority sched_setparam sched_setscheduler sched_setaffinity sched_setattr ioprio_set
        migrate_pages move_pages kcmp setxattr lsetxattr fsetxattr removexattr lremovexattr fremovexattr
        inotify_init inotify_init1 fanotify_init fanotify_mark sethostname setdomainname capset'''.split()
        for name in denied:
            number=sec.seccomp_syscall_resolve_name(name.encode())
            if number>=0 and sec.seccomp_rule_add(ctx,0x50000|1,number,0)!=0:raise RuntimeError('seccomp rule failed')
        class Comparison(C.Structure):_fields_=[('arg',C.c_uint),('op',C.c_int),('a',C.c_uint64),('b',C.c_uint64)]
        sec.seccomp_rule_add_array.argtypes=[C.c_void_p,C.c_uint32,C.c_int,C.c_uint,C.POINTER(Comparison)]
        number=sec.seccomp_syscall_resolve_name(b'clone')
        for bit in [0x20000,0x2000000,0x4000000,0x8000000,0x10000000,0x20000000,0x40000000]:
            compare=Comparison(0,7,bit,bit)
            if sec.seccomp_rule_add_array(ctx,0x50000|1,number,1,C.byref(compare))!=0:raise RuntimeError('clone namespace filter failed')
        number=sec.seccomp_syscall_resolve_name(b'clone3')
        if number>=0 and sec.seccomp_rule_add(ctx,0x50000|38,number,0)!=0:raise RuntimeError('clone3 filter failed')
        number=sec.seccomp_syscall_resolve_name(b'prlimit64');compare=Comparison(0,1,0,0)
        if sec.seccomp_rule_add_array(ctx,0x50000|1,number,1,C.byref(compare))!=0:raise RuntimeError('prlimit filter failed')
        if sec.seccomp_load(ctx)!=0:raise RuntimeError('seccomp load failed')
    finally:sec.seccomp_release(ctx)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,)*2)
    resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024**2,)*2);resource.setrlimit(resource.RLIMIT_NOFILE,(64,64));resource.setrlimit(resource.RLIMIT_CPU,(cpu_seconds,cpu_seconds))

def main():
    source,export,root,report=sys.argv[1:];config=json.loads(os.environ['NEURO_WORKSPACE_ACTION'])
    cpu_seconds=int(os.environ.get('NEURO_WORKSPACE_CPU_SECONDS','20'))
    if not 20<=cpu_seconds<=601:raise RuntimeError('bounded CPU budget required')
    # Gather proof before /proc is hidden; cgroup and namespace IDs are not
    # supplied by the command and cannot be overwritten via stdout.
    proof={name+'_namespace':os.readlink('/proc/self/ns/'+short) for name,short in [('mount','mnt'),('pid','pid'),('network','net'),('user','user')]}
    group=Path('/sys/fs/cgroup')/Path('/proc/self/cgroup').read_text().strip().split('::')[-1].lstrip('/')
    proof.update(memory_max=(group/'memory.max').read_text().strip(),pids_max=(group/'pids.max').read_text().strip(),swap_max=(group/'memory.swap.max').read_text().strip())
    if proof['memory_max']!='402653184' or proof['pids_max']!='32' or proof['swap_max']!='0':raise RuntimeError('required cgroup limits missing')
    outfd=os.open(export,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);reportfd=os.open(report,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    peakfd=os.open(group/'memory.peak',os.O_RDONLY);eventsfd=os.open(group/'memory.events',os.O_RDONLY)
    initial=snapshot(source);root=Path(root)
    mount(None,'/',flags=16384|262144) # recursive private
    mount('tmpfs',root,'tmpfs',2|4,'size=96m,nr_inodes=8192,mode=0700')
    for item in ['/usr/bin','/usr/lib','/usr/include','/usr/libexec/gcc','/usr/share/locale']:
        if Path(item).exists():bind_ro(item,root/item.lstrip('/'))
    (root/'bin').symlink_to('usr/bin');(root/'lib').symlink_to('usr/lib');(root/'lib64').symlink_to('usr/lib64')
    if Path('/usr/lib64').exists():bind_ro('/usr/lib64',root/'usr/lib64')
    (root/'work').mkdir();mount('tmpfs',root/'work','tmpfs',2|4,'size=32m,nr_inodes=2048,mode=0700')
    materialize(initial,root/'work');del initial;gc.collect()
    (root/'work/.tmp').mkdir(exist_ok=True);(root/'tmp').symlink_to('work/.tmp')
    (root/'proc').mkdir();(root/'etc').mkdir();(root/'dev').mkdir()
    # Debian command aliases (awk/cc/etc.) point through /etc/alternatives.
    # Copy only public symlinks into our private root; never bind host /etc.
    alternatives=root/'etc/alternatives';alternatives.mkdir()
    if Path('/etc/alternatives').is_dir():
        for item in Path('/etc/alternatives').iterdir():
            if item.is_symlink():
                target=os.readlink(item)
                if target.startswith(('/usr/bin/','/usr/lib/','/usr/libexec/gcc/')):os.symlink(target,alternatives/item.name)
    # Only these devices, never host /dev, are exposed.
    for dev in ['null','urandom']:
        target=root/'dev'/dev;target.touch();mount('/dev/'+dev,target,flags=4096);mount(None,target,flags=4096|32|2)
    os.chroot(root);os.chdir('/work');proof['capabilities_after_drop']=drop_caps()
    proof['readonly_mounts']={p:bool(os.statvfs(p).f_flag&os.ST_RDONLY) for p in ['/usr/bin','/usr/lib']}
    limits=os.statvfs('/work');proof['scratch_capacity_bytes']=limits.f_blocks*limits.f_frsize;proof['scratch_inode_limit']=limits.f_files
    if not all(proof['readonly_mounts'].values()) or proof['scratch_capacity_bytes']>32*1024**2:raise RuntimeError('mount guarantees not confirmed')
    cwd=config.get('cwd','.')
    cwd='/work' if cwd in {'.','/work'} else cwd if cwd.startswith('/work/') else '/work/'+cwd
    resolved=os.path.realpath(cwd)
    if resolved!='/work' and not resolved.startswith('/work/'):raise RuntimeError('cwd escapes scratch')
    # A fresh fork is necessary: filter only the command, never the trusted
    # supervisor that exports after all namespace descendants have died.
    ready_r,ready_w=os.pipe()
    pid=os.fork()
    if pid==0:
        try:
            os.close(ready_r);child_filter(cpu_seconds);os.chdir(resolved)
            os.write(ready_w,b"READY");os.close(ready_w)
            env={'PATH':'/usr/bin:/bin','HOME':'/work','TMPDIR':'/work/.tmp','LANG':'C.UTF-8',**config.get('env',{})}
            os.execve('/bin/bash',['bash','--noprofile','--norc','-o','pipefail','-c',config['command']],env)
        except BaseException as exc:print('WORKSPACE_CHILD_SETUP_FAILED: '+str(exc),file=sys.stderr);os._exit(125)
    os.close(ready_w)
    _,status=os.waitpid(pid,0)
    proof["child_setup_complete"]=os.read(ready_r,16)==b"READY";os.close(ready_r)
    try:os.kill(-1,signal.SIGKILL)
    except ProcessLookupError:pass
    while True:
        try:os.waitpid(-1,0)
        except ChildProcessError:break
    result=snapshot('/work');os.fchdir(outfd);materialize(result,'.')
    # Flush exported files before granting a durable completion receipt.
    for path,value in result.items():
        if value['kind']=='file':
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
    os.fsync(outfd);proof['cpu_seconds_limit']=cpu_seconds
    proof['memory_peak_bytes']=int(os.read(peakfd,4096));proof['memory_events']=os.read(eventsfd,4096).decode();os.close(peakfd);os.close(eventsfd)
    os.write(reportfd,json.dumps({'isolation':proof,'export_complete':True,'exit_code':os.waitstatus_to_exitcode(status)}).encode());os.fsync(reportfd);os.close(reportfd);os.close(outfd)
    return os.waitstatus_to_exitcode(status)
if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print('WORKSPACE_SETUP_OR_EXPORT_FAILED: '+str(exc),file=sys.stderr);sys.exit(125)
