"""Bounded shell executor using a separate read-only Landlock worker."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import tempfile


class ReadOnlyShell:
    def __init__(self, workspace, timeout=10, output_limit=262144, *, system=False):
        self.workspace = Path(workspace).resolve()
        self.system = system
        self.timeout = timeout
        self.output_limit = output_limit

    def execute(self, action):
        if not self.system:return self._execute(action)
        with tempfile.TemporaryDirectory(prefix='neuroos-public-system-') as directory:
            from .system_snapshot import capture,contract
            root=Path(directory);facts=capture(root)
            result=self._execute(action,directory);result['system_snapshot_sources']=facts
            if result['exit_code']!=0:
                result['diagnostic_access']=contract()
                result['error']={'category':'diagnostic_command_failure',
                                 'message':'This command failed inside the confined diagnostics shell. Direct host /proc and D-Bus are deliberately unavailable. This does NOT establish that the host data, service or device are absent. Read the provided fact files using the snapshot environment variable; their source receipts report actual availability. Do not mount /proc or request broader privileges to read these snapshots.',
                                 'nonempty_successful_fact_files':[name for name,meta in facts.items() if meta.get('exit_code')==0 and meta.get('captured_bytes',0)>0]}
            return result

    def _execute(self, action, snapshot_dir=None):
        cwd = (self.workspace / action.get('cwd', '.')).resolve()
        if not cwd.is_relative_to(self.workspace) or not cwd.is_dir():
            return {'exit_code': 126, 'stdout': '', 'stderr': 'cwd outside workspace or missing',
                    'timed_out': False, 'output_limited': False}
        started = time.monotonic()
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name('readonly_worker.py')),
             str(self.workspace), str(cwd), action['command']],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={'NEURO_ENV': json.dumps(action.get('env', {})), 'NEURO_SYSTEM_READ':'1' if self.system else '0','NEURO_SYSTEM_SNAPSHOT':snapshot_dir or ''},
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
