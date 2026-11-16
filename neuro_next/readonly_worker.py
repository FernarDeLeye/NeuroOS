"""Stack a read-only Landlock policy on the original sandbox. Fail closed."""
import ctypes as C,json,os,sys
from pathlib import Path
from sandbox import confine,Rule

def read_only(workspace,paths=()):
    libc=C.CDLL(None,use_errno=True);libc.syscall.restype=C.c_long
    def checked(v):
        if v<0:raise OSError(C.get_errno(),os.strerror(C.get_errno()))
        return v
    abi=checked(libc.syscall(444,0,0,1));handled=(1<<(16 if abi>=5 else 15))-1
    attr=C.c_uint64(handled);ruleset=checked(libc.syscall(444,C.byref(attr),C.sizeof(attr),0));read=(1<<0)|(1<<2)|(1<<3)
    try:
        for path,rights in [(workspace,read),('/usr',read),('/bin',read),('/lib',read),('/lib64',read),('/dev/null',(1<<1)|(1<<2))]+[(p,read if Path(p).is_dir() else (1<<2)) for p in paths]:
            if not Path(path).exists():continue
            fd=os.open(path,os.O_PATH|os.O_CLOEXEC)
            try:
                rule=Rule(rights,fd);checked(libc.syscall(445,ruleset,1,C.byref(rule),0))
            finally:os.close(fd)
        checked(libc.syscall(446,ruleset,0))
    finally:os.close(ruleset)

def main():
    try:
        workspace,cwd,command=sys.argv[1:];os.chdir(cwd)
        paths=[]
        if os.environ.get('NEURO_SYSTEM_READ')=='1':
            # Explicit public diagnostics only: no whole /etc, /proc, home or
            # process fd/environ/cmdline trees. Resolve self before Landlock.
            paths=[str(Path(p).resolve()) for p in ['/etc/os-release','/etc/debian_version','/proc/meminfo','/proc/cpuinfo','/proc/loadavg','/proc/uptime','/proc/version','/proc/stat','/proc/self/mountinfo','/proc/self/mounts'] if Path(p).exists()]
        if os.environ.get('NEURO_SYSTEM_READ')=='1' and os.environ.get('NEURO_SYSTEM_SNAPSHOT'):paths.append(os.environ['NEURO_SYSTEM_SNAPSHOT'])
        confine(workspace,paths);read_only(workspace,paths)
        os.execve('/bin/bash',['bash','--noprofile','--norc','-o','pipefail','-c',command],
            {'PATH':'/usr/bin:/bin','HOME':workspace,'TMPDIR':workspace,'LANG':'C.UTF-8','NEURO_SYSTEM_SNAPSHOT':os.environ.get('NEURO_SYSTEM_SNAPSHOT',''),**json.loads(os.environ['NEURO_ENV'])})
    except Exception as exc:print('READ_ONLY_SANDBOX_FAILED: '+str(exc),file=sys.stderr);sys.exit(125)
if __name__=='__main__':main()
