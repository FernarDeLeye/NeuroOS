"""Bounded no-follow tree snapshots shared by trusted supervisor and publisher."""
import base64,hashlib,os,stat
from pathlib import Path
MAX_FILE=8*1024**2
MAX_TOTAL=32*1024**2
MAX_ENTRIES=1024

def logical_path(value,root=False):
    if not isinstance(value,str) or '\x00' in value:raise ValueError('path must be a string')
    if value in {'.','/work'}:
        if root:return ''
        raise ValueError('file path required')
    if value.startswith('/work/'):value=value[6:]
    elif value.startswith('/'):raise ValueError('absolute paths must start with /work/')
    if not value or any(p in {'','.','..'} for p in value.split('/')):raise ValueError('use canonical paths without dot/parent components')
    if len(value)>1024 or len(value.split('/'))>32:raise ValueError('path bound exceeded')
    value.encode('utf-8');return value

def parent_fd(root,name):
    parts=name.split('/');fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
        return fd,parts[-1]
    except BaseException:os.close(fd);raise

def read_entry(fd,name,observe_metadata=False):
    info=os.stat(name,dir_fd=fd,follow_symlinks=False)
    mode=stat.S_IMODE(info.st_mode)&0o777
    observed={'device':info.st_dev,'inode':info.st_ino,'mtime_ns':info.st_mtime_ns,'ctime_ns':info.st_ctime_ns} if observe_metadata else {}
    if stat.S_ISDIR(info.st_mode):return {'kind':'dir','mode':mode,**observed}
    if stat.S_ISLNK(info.st_mode):return {'kind':'symlink','target':os.readlink(name,dir_fd=fd),**observed}
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_FILE:raise ValueError('only bounded single-link regular files, directories and symlinks can be snapshotted')
    f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    try:
        opened=os.fstat(f)
        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):raise ValueError('source changed before read')
        raw=b''
        while len(raw)<=MAX_FILE:
            part=os.read(f,min(65536,MAX_FILE+1-len(raw)))
            if not part:break
            raw+=part
        after=os.fstat(f)
        if len(raw)>MAX_FILE or (info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('source changed during read')
        return {'kind':'file','mode':mode,'data':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),**observed}
    finally:os.close(f)

def snapshot(root,observe_metadata=False):
    result={};total=0
    def visit(fd,prefix,depth):
        nonlocal total
        if depth>32:raise ValueError('tree depth bound exceeded')
        for name in sorted(os.listdir(fd)):
            path=(prefix+'/'+name).lstrip('/');logical_path(path)
            if name.startswith('.neuro-control-'):raise ValueError('reserved control entry')
            value=read_entry(fd,name,observe_metadata);result[path]=value;total+=value.get('size',0)
            if len(result)>MAX_ENTRIES or total>MAX_TOTAL:raise ValueError('workspace bounds exceeded')
            if value['kind']=='dir':
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:visit(child,path,depth+1)
                finally:os.close(child)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:visit(fd,'',0)
    finally:os.close(fd)
    return result

def materialize(tree,root):
    for path,value in sorted(tree.items(),key=lambda item:(item[0].count('/'),item[0])):
        fd,name=parent_fd(root,path)
        try:
            if value['kind']=='dir':os.mkdir(name,0o700,dir_fd=fd)
            elif value['kind']=='symlink':os.symlink(value['target'],name,dir_fd=fd)
            else:
                f=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                try:
                    raw=base64.b64decode(value['data']);offset=0
                    while offset<len(raw):offset+=os.write(f,raw[offset:])
                    os.fchmod(f,value['mode']&0o777)
                finally:os.close(f)
        finally:os.close(fd)

    # Restore directory modes only after creating their contents.
    for path,value in sorted(tree.items(),key=lambda item:-item[0].count('/')):
        if value['kind']=='dir':
            fd,name=parent_fd(root,path)
            try:os.chmod(name,value['mode']&0o777,dir_fd=fd,follow_symlinks=False)
            finally:os.close(fd)

def metadata(tree):return {p:{k:v for k,v in value.items() if k!='data'} for p,value in tree.items()}
