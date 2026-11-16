"""Linux x86-64 worker confinement. Failure never falls back to plain shell."""
import ctypes as C
import errno
import os
import platform
import resource
import sys
from pathlib import Path


class Rule(C.Structure):
    _pack_ = 1
    _fields_ = [('access', C.c_uint64), ('parent', C.c_int32)]


class Comparison(C.Structure):
    _fields_ = [('arg', C.c_uint), ('op', C.c_int),
                ('datum_a', C.c_uint64), ('datum_b', C.c_uint64)]


def confine(workspace, readonly_paths=()):
    if platform.machine() != 'x86_64' or os.geteuid() == 0:
        raise RuntimeError('sandbox requires non-root x86-64 Linux')
    # RLIMIT_NPROC counts all threads of the real UID, not this task alone.
    # Sample before Landlock hides /proc; this is only a coarse emergency bound.
    uid_threads = 0
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = dict(line.split(':', 1) for line in (entry / 'status').read_text().splitlines() if ':' in line)
            if int(fields['Uid'].split()[0]) == os.getuid():
                uid_threads += int(fields.get('Threads', '1'))
        except (OSError, ValueError, KeyError):
            continue
    nproc_limit = max(64, uid_threads + 64)
    _, inherited_hard = resource.getrlimit(resource.RLIMIT_NPROC)
    if inherited_hard != resource.RLIM_INFINITY:
        nproc_limit = min(nproc_limit, inherited_hard)
    libc = C.CDLL(None, use_errno=True)
    libc.syscall.restype = C.c_long

    def checked(value):
        if value < 0:
            raise OSError(C.get_errno(), os.strerror(C.get_errno()))
        return value

    abi = checked(libc.syscall(444, 0, 0, 1))
    if abi < 3:
        raise RuntimeError('Landlock ABI >= 3 required')
    handled = (1 << (16 if abi >= 5 else 15)) - 1
    attr = C.c_uint64(handled)
    ruleset = checked(libc.syscall(444, C.byref(attr), C.sizeof(attr), 0))
    read = (1 << 0) | (1 << 2) | (1 << 3)
    # No creation of devices or socket files, even within workspace.
    workspace_access = handled & ~((1 << 6) | (1 << 9) | (1 << 11) | (1 << 15))
    try:
        for path, rights in [(workspace, workspace_access), ('/usr', read),
                             ('/bin', read), ('/lib', read), ('/lib64', read),
                             ('/dev/null', (1 << 1) | (1 << 2))]+[(p,read if Path(p).is_dir() else (1<<2)) for p in readonly_paths]:
            if not Path(path).exists():
                continue
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Rule(rights, fd)
                checked(libc.syscall(445, ruleset, 1, C.byref(rule), 0))
            finally:
                os.close(fd)
        checked(libc.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
        checked(libc.syscall(446, ruleset, 0))
    finally:
        os.close(ruleset)

    sec = C.CDLL('libseccomp.so.2', use_errno=True)
    sec.seccomp_init.argtypes = [C.c_uint32]
    sec.seccomp_init.restype = C.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [C.c_char_p]
    sec.seccomp_rule_add.argtypes = [C.c_void_p, C.c_uint32, C.c_int, C.c_uint]
    sec.seccomp_load.argtypes = [C.c_void_p]
    sec.seccomp_release.argtypes = [C.c_void_p]
    ctx = sec.seccomp_init(0x7fff0000)  # ALLOW; forbidden syscalls return EPERM
    if not ctx:
        raise RuntimeError('seccomp_init failed')
    denied = '''socket socketpair connect bind listen accept accept4 sendto sendmsg
        sendmmsg recvmsg recvmmsg io_uring_setup io_uring_enter io_uring_register
        ptrace process_vm_readv process_vm_writev pidfd_open pidfd_getfd pidfd_send_signal
        kill tkill tgkill rt_sigqueueinfo rt_tgsigqueueinfo mount umount2 pivot_root chroot
        unshare setns bpf perf_event_open userfaultfd reboot kexec_load kexec_file_load
        init_module finit_module delete_module open_by_handle_at name_to_handle_at
        keyctl add_key request_key mknod mknodat setsid setpgid
        chmod fchmod fchmodat fchmodat2 chown fchown lchown fchownat
        truncate mount_setattr move_mount fsopen fsconfig fsmount open_tree
        acct swapon swapoff quotactl settimeofday clock_settime
        shmget shmat shmdt shmctl msgget msgsnd msgrcv msgctl semget semop semtimedop semctl
        utime utimes futimesat utimensat
        setpriority sched_setparam sched_setscheduler sched_setaffinity sched_setattr
        ioprio_set migrate_pages move_pages kcmp
        setxattr lsetxattr fsetxattr removexattr lremovexattr fremovexattr
        inotify_init inotify_init1 fanotify_init fanotify_mark sethostname setdomainname'''.split()
    try:
        for name in denied:
            number = sec.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and sec.seccomp_rule_add(ctx, 0x50000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError('seccomp rule failed: ' + name)
        # prlimit64(pid=0) is needed by libc for own limits. Other PIDs are forbidden.
        sec.seccomp_rule_add_array.argtypes = [C.c_void_p, C.c_uint32, C.c_int, C.c_uint, C.POINTER(Comparison)]
        number = sec.seccomp_syscall_resolve_name(b'prlimit64')
        comparison = Comparison(0, 1, 0, 0)  # SCMP_CMP_NE: argument 0 != 0
        if number < 0 or sec.seccomp_rule_add_array(ctx, 0x50000 | errno.EPERM, number, 1, C.byref(comparison)) != 0:
            raise RuntimeError('seccomp prlimit rule failed')
        if sec.seccomp_load(ctx) != 0:
            raise RuntimeError('seccomp_load failed')
    finally:
        sec.seccomp_release(ctx)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (nproc_limit, nproc_limit))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))


def main():
    try:
        workspace, cwd, command = sys.argv[1:]
        os.chdir(cwd)
        confine(workspace)
        os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-o', 'pipefail', '-c', command],
                  {'PATH': '/usr/bin:/bin', 'HOME': workspace, 'TMPDIR': workspace,
                   'LANG': 'C.UTF-8', **__import__('json').loads(os.environ['NEURO_ENV'])})
    except Exception as exc:
        print('SANDBOX_SETUP_FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(125)


if __name__ == '__main__':
    main()
