"""Bounded PTY transport for the existing isolated executor, not a host shell."""
import errno,fcntl,json,os,pty,selectors,signal,struct,subprocess,termios,time

def run(argv,env,stdin,timeout,logs,input_fifo,cancel_event,limit=262144):
    master,slave=pty.openpty();attrs=termios.tcgetattr(slave);attrs[3]&=~termios.ECHO;termios.tcsetattr(slave,termios.TCSANOW,attrs)
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,0,0));os.set_blocking(master,False)
    os.mkfifo(input_fifo,0o600);fifo=os.open(input_fifo,os.O_RDWR|os.O_NONBLOCK)
    process=subprocess.Popen(argv,env=env,stdin=slave,stdout=slave,stderr=slave,start_new_session=True,close_fds=True);os.close(slave)
    selector=selectors.DefaultSelector();selector.register(master,selectors.EVENT_READ,'output');selector.register(fifo,selectors.EVENT_READ,'input')
    output=bytearray();queued=bytearray(stdin.encode());messages=bytearray();sent=0;start=time.monotonic();timed_out=cancelled=limited=False
    log=open(logs['stdout'],'ab',buffering=0)
    def kill():
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    try:
        while selector.get_map():
            if time.monotonic()-start>timeout:timed_out=True;kill()
            if cancel_event is not None and cancel_event.is_set():cancelled=True;kill()
            if process.poll() is not None:
                kill()
                if fifo is not None:selector.unregister(fifo);os.close(fifo);fifo=None
            if queued:
                try:
                    n=os.write(master,queued[:4096]);del queued[:n];sent+=n
                    if sent>65536:limited=True;kill()
                except BlockingIOError:pass
                except OSError:
                    if process.poll() is None:raise
                    queued.clear()
            for key,_ in selector.select(.05):
                if key.data=='input':
                    messages.extend(os.read(fifo,4096))
                    if len(messages)>8192:limited=True;kill();messages.clear();continue
                    while b'\n' in messages:
                        line,_,rest=messages.partition(b'\n');messages=bytearray(rest)
                        item=json.loads(line);queued.extend(b'\x04' if item.get('eof') else item['text'].encode())
                else:
                    try:data=os.read(master,8192)
                    except OSError as exc:
                        if exc.errno!=errno.EIO:raise
                        data=b''
                    if not data:selector.unregister(master);continue
                    available=limit-len(output);piece=data[:max(0,available)];output.extend(piece);log.write(piece);os.fsync(log.fileno())
                    if len(data)>available:limited=True;kill()
        process.wait(timeout=2)
    finally:
        kill();process.wait(timeout=2);selector.close();log.close();os.close(master)
        if fifo is not None:os.close(fifo)
        os.unlink(input_fifo)
    return {'exit_code':process.returncode,'stdout':output.decode(errors='replace'),'stderr':'','stderr_merged':True,'stdio_mode':'pty','timed_out':timed_out,'cancelled':cancelled,'output_limited':limited,'duration_s':time.monotonic()-start}
