"""Cooperative inference/switch lease and bounded server-idle barrier.

No model load/unload, process kill, service restart or privileged operation.
Closing the dedicated HTTP socket requests llama.cpp disconnect cancellation;
only measured idle slots authorize another request or a model switch.
"""
import contextlib
import fcntl
import http.client
import json
import math
import os
from pathlib import Path
import socket
import stat
import threading
import time
from urllib.parse import quote, urlsplit

from .installation import installed,state_root
LOCK = state_root()/'inference.lock'

class DeadlineExceeded(TimeoutError):
    """An enclosing task/model/campaign work deadline expired."""

class InferenceCancelled(RuntimeError):
    """A resource/controller stop request; not a model planning error."""

def remaining(deadline, cap):
    if not math.isfinite(cap) or cap<=0:raise ValueError('positive finite operation bound required')
    if deadline is None:return cap
    if not math.isfinite(deadline):raise ValueError('finite enclosing deadline required')
    left=deadline-time.monotonic()
    if left<=0:raise DeadlineExceeded('enclosing work deadline expired')
    return min(cap,left)


@contextlib.contextmanager
def request_lock(path=None, timeout=30):
    if timeout <= 0: raise ValueError('positive lock timeout required')
    fd = os.open(path or LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('inference lock must be an owned regular file')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError('inference/switch lease busy')
                time.sleep(min(.05, remaining))
        yield
    finally:
        os.close(fd)


def get_json(endpoint, route, timeout=2, body=None):
    url = urlsplit(endpoint)
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
    sock_ref=[None];expired=threading.Event();response=None
    def cancel():
        expired.set();sock=sock_ref[0] or conn.sock
        if sock is not None:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
    timer=threading.Timer(timeout,cancel);timer.daemon=True;timer.start()
    try:
        headers={'Connection':'close'};payload=None
        if body is not None:
            if route.split('?')[0] not in {'/apply-template','/tokenize'}:raise ValueError('metadata POST route not allowed')
            payload=json.dumps(body).encode();headers['Content-Type']='application/json'
        conn.request('GET' if body is None else 'POST',url.path.rstrip('/')+route,body=payload,headers=headers)
        sock_ref[0]=conn.sock
        response = conn.getresponse()
        if response.status != 200: raise RuntimeError('slot telemetry HTTP ' + str(response.status))
        raw = response.read(1024 * 1024 + 1)
        if expired.is_set():raise TimeoutError('slot telemetry wall-clock deadline')
        if len(raw) > 1024 * 1024: raise ValueError('slot telemetry too large')
        return json.loads(raw)
    finally:
        timer.cancel()
        sock=sock_ref[0] or conn.sock
        if sock is not None:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        if response is not None:response.close()
        conn.close()


def wait_for_idle(endpoint, model, timeout=30, getter=None, poll=.1):
    if timeout <= 0 or poll <= 0: raise ValueError('positive idle bounds required')
    getter = getter or get_json
    started = time.monotonic()
    deadline = started + timeout
    probes = consecutive = 0
    last = 'no samples'
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('inference not confirmed idle: ' + last)
        probes += 1
        try:
            slots = getter(endpoint, '/slots?model=' + quote(model, safe='') + '&autoload=false', timeout=min(2, remaining))
            valid = isinstance(slots, list) and bool(slots) and all(isinstance(s, dict) and type(s.get('is_processing')) is bool for s in slots)
            idle = valid and not any(s['is_processing'] for s in slots)
            last = 'busy' if valid and not idle else ('idle' if idle else 'invalid slot telemetry')
        except Exception as exc:
            idle = False
            last = type(exc).__name__ + ': ' + str(exc)[:200]
        consecutive = consecutive + 1 if idle else 0
        if consecutive >= 2:
            return {'idle': True, 'probes': probes, 'duration_s': time.monotonic() - started}
        time.sleep(min(poll, max(0, deadline - time.monotonic())))


def completion_request(endpoint, model, payload, timeout, lock_path=None, receipt=None, idle_timeout=30, deadline=None, cancel_event=None):
    if timeout <= 0: raise ValueError('positive inference deadline required')
    receipt = receipt if receipt is not None else {}
    if cancel_event is not None and cancel_event.is_set():raise InferenceCancelled('controller/resource stop requested')
    with request_lock(lock_path, timeout=remaining(deadline,idle_timeout)):
        receipt['before_request'] = wait_for_idle(endpoint, model, timeout=remaining(deadline,idle_timeout))
        if cancel_event is not None and cancel_event.is_set():raise InferenceCancelled('controller/resource stop requested')
        timeout=remaining(deadline,timeout)
        url = urlsplit(endpoint)
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
        expired = threading.Event()
        active_socket = [None]
        def cancel():
            expired.set()
            sock = active_socket[0] or conn.sock
            if sock is not None:
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        timer = threading.Timer(timeout, cancel)
        timer.daemon = True
        timer.start()
        watch_stop=threading.Event()
        def watch_cancel():
            while not watch_stop.wait(.05):
                if cancel_event is not None and cancel_event.is_set():cancel();return
        watcher=threading.Thread(target=watch_cancel,daemon=True) if cancel_event is not None else None
        if watcher:watcher.start()
        response = None
        try:
            receipt['post_attempted'] = True
            conn.request('POST', url.path.rstrip('/') + '/v1/chat/completions?autoload=false',
                         body=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'Connection': 'close'})
            active_socket[0] = conn.sock
            response = conn.getresponse()
            if response.status != 200: raise RuntimeError('inference HTTP ' + str(response.status))
            raw = response.read(2 * 1024**2 + 1)
            if expired.is_set(): raise TimeoutError('inference wall-clock deadline')
            if len(raw) > 2 * 1024**2: raise ValueError('backend response too large')
            return raw
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():raise InferenceCancelled('controller/resource stop requested') from exc
            if deadline is not None and time.monotonic()>=deadline:raise DeadlineExceeded('enclosing work deadline expired') from exc
            if expired.is_set():raise TimeoutError('inference wall-clock deadline') from exc
            raise
        finally:
            timer.cancel()
            watch_stop.set()
            if watcher:watcher.join(timeout=.2)
            # Force close even when no HTTP response headers were received.
            sock = active_socket[0] or conn.sock
            if sock is not None:
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            if response is not None:
                response.close()
            conn.close()
            receipt['connection_closed'] = True
            receipt['deadline_expired'] = expired.is_set()
            # Keep the same lease while llama.cpp observes disconnection and
            # cancels/drains. Unknown or busy is never treated as idle.
            try:
                receipt['after_close'] = wait_for_idle(endpoint, model, timeout=idle_timeout)
            except Exception as exc:
                receipt['after_close'] = {'idle': False, 'error': str(exc)}
                raise
