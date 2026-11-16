import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time


class Shell:
    def __init__(self, workspace, timeout=10, output_limit=262144):
        self.workspace = Path(workspace).resolve()
        self.timeout = timeout
        self.output_limit = output_limit

    def execute(self, action):
        cwd = (self.workspace / action.get('cwd', '.')).resolve()
        if not cwd.is_relative_to(self.workspace) or not cwd.is_dir():
            return {'exit_code': 126, 'stdout': '', 'stderr': 'cwd outside workspace or missing',
                    'timed_out': False, 'output_limited': False}
        started = time.monotonic()
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name('sandbox.py')),
             str(self.workspace), str(cwd), action['command']],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={'NEURO_ENV': json.dumps(action.get('env', {}))},
            start_new_session=True, close_fds=True)
        selector = selectors.DefaultSelector()
        streams = {'stdout': bytearray(), 'stderr': bytearray()}
        for pipe, name in [(proc.stdout, 'stdout'), (proc.stderr, 'stderr')]:
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        data = action.get('stdin', '').encode()
        if data:
            os.set_blocking(proc.stdin.fileno(), False)
            selector.register(proc.stdin, selectors.EVENT_WRITE, 'stdin')
        else:
            proc.stdin.close()
        offset = 0
        timed_out = limited = False

        def kill_group():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        try:
            while selector.get_map():
                if time.monotonic() - started > self.timeout:
                    timed_out = True
                    kill_group()
                if proc.poll() is not None:
                    kill_group()  # close descendants retaining stdout after shell exit
                for key, _ in selector.select(0.05):
                    pipe, name = key.fileobj, key.data
                    if name == 'stdin':
                        try:
                            offset += os.write(pipe.fileno(), data[offset:offset + 4096])
                        except BrokenPipeError:
                            offset = len(data)
                        if offset == len(data):
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    chunk = os.read(pipe.fileno(), 8192)
                    if not chunk:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    available = self.output_limit - sum(map(len, streams.values()))
                    streams[name].extend(chunk[:max(0, available)])
                    if len(chunk) > available:
                        limited = True
                        kill_group()
            proc.wait(timeout=2)
        finally:
            kill_group()
            proc.wait(timeout=2)
            selector.close()
            for pipe in [proc.stdin, proc.stdout, proc.stderr]:
                if not pipe.closed:
                    pipe.close()
        return {'exit_code': proc.returncode, **{k: v.decode(errors='replace') for k, v in streams.items()},
                'timed_out': timed_out, 'output_limited': limited,
                'duration_s': round(time.monotonic() - started, 4), 'pid': proc.pid}
