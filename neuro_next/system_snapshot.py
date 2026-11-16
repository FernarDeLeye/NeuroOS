"""Fixed read-only host queries exported as data to the confined system shell.

Agent-provided commands never run on the host or receive a D-Bus socket. No scan,
connection, radio toggle, service control, credentials or process arguments.
"""
import json
import subprocess
import time

QUERIES = {
    'os.txt': ['/usr/bin/cat','/etc/os-release'],
    'memory.txt': ['/usr/bin/cat','/proc/meminfo'],
    'cpu.txt': ['/usr/bin/cat','/proc/cpuinfo'],
    'processes.tsv': ['/usr/bin/ps', '-eo', 'pid,ppid,comm,pcpu,pmem,rss', '--sort=-pcpu'],
    'processes-by-memory.tsv': ['/usr/bin/ps', '-eo', 'pid,ppid,comm,pcpu,pmem,rss', '--sort=-rss'],
    'filesystems.txt': ['/usr/bin/df', '-P'],
    'interfaces.json': ['/usr/sbin/ip', '-j', 'address', 'show'],
    'network-devices.txt': ['/usr/bin/nmcli', '--wait', '3', '-t', '--escape', 'yes', '-f', 'DEVICE,TYPE,STATE', 'device', 'status'],
    'wifi.txt': ['/usr/bin/nmcli', '--wait', '3', '-t', '--escape', 'yes', '-f', 'SSID,SIGNAL,SECURITY,IN-USE,BSSID', 'device', 'wifi', 'list', '--rescan', 'no'],
    'wifi-radio.txt': ['/usr/bin/nmcli', '--wait', '3', 'radio', 'wifi'],
}


def contract():
    return {'tool': 'system', 'execution_scope': 'confined read-only diagnostics, not an unrestricted host shell',
            'snapshot_environment_variable': 'NEURO_SYSTEM_SNAPSHOT',
            'fact_files': {'processes.tsv': 'actual host process table; whitespace columns PID PPID COMMAND %CPU %MEM RSS_KiB',
                           'processes-by-memory.tsv': 'same host table sorted by RSS, largest first',
                           'filesystems.txt': 'host disk usage', 'interfaces.json': 'host interface addresses/state',
                           'network-devices.txt': 'NetworkManager devices', 'wifi.txt': 'already observed Wi-Fi SSIDs',
                           'wifi-radio.txt': 'radio state', 'user-services.txt': 'user service states'},
            'source_errors': 'sources.json; check exit_code, error and captured_bytes before inferring absence',
            'direct_host_proc_and_bus': False}


def escaped_fields(line):
    fields, current, escaped = [], '', False
    for char in line:
        if escaped:current += char;escaped = False
        elif char == '\\':escaped = True
        elif char == ':':fields.append(current);current = ''
        else:current += char
    fields.append(current)
    return fields


def summarize(root, sources):
    """Bounded factual context, independent of any user wording or expected answer."""
    facts = {}
    for name, lines, chars in [('processes.tsv', 4, 380), ('processes-by-memory.tsv', 4, 380),
                               ('network-devices.txt', 6, 240), ('wifi-radio.txt', 1, 40),
                               ('user-services.txt', 3, 200), ('filesystems.txt', 2, 200)]:
        source = sources.get(name, {})
        raw = (root / name).read_text() if (root / name).exists() else ''
        sample = '\n'.join(raw.splitlines()[:lines])[:chars]
        facts[name] = {'exit_code': source.get('exit_code'), 'text': sample,
                       'partial': sample.strip() != raw.strip()}
        if source.get('error'):facts[name]['error'] = source['error']
    raw = (root / 'wifi.txt').read_text() if (root / 'wifi.txt').exists() else ''
    records = [escaped_fields(line) for line in raw.splitlines()]
    valid = [r for r in records if len(r) == 4 and r[1].isdigit() and 0 <= int(r[1]) <= 100]
    names = sorted({r[0] for r in valid if r[0]})
    selected, size = [], 0
    for name in names:
        if len(selected) >= 24 or size + len(name) > 600:break
        selected.append(name);size += len(name)
    source = sources.get('wifi.txt', {})
    facts['wifi'] = {'exit_code': source.get('exit_code'), 'ssids': selected,
                     'hidden_ap_count': sum(not r[0] for r in valid), 'cached_not_new_scan': True,
                     'partial': len(selected) != len(names) or len(valid) != len(records)}
    if source.get('error'):facts['wifi']['error'] = source['error']
    return facts


def capture(root, names=None):
    facts = {}
    env = {'PATH': '/usr/bin:/usr/sbin:/bin:/sbin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}

    def query(name, argv, environment):
        started = time.time()
        try:
            result = subprocess.run(argv, capture_output=True, text=True, errors='replace', timeout=4, env=environment)
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
            error = None
        except (OSError, subprocess.TimeoutExpired) as exc:
            stdout, stderr, code, error = '', str(exc), None, type(exc).__name__
        limit = 131072
        (root / name).write_text(stdout[:limit])
        facts[name] = {'command': argv, 'exit_code': code, 'stderr': stderr[:4096],
                       'error': error, 'captured_at': started, 'completed_at': time.time(),
                       'output_truncated': len(stdout) > limit, 'captured_bytes': len(stdout[:limit].encode()), 'read_only': True}

    for name, argv in QUERIES.items():
        if names is None or name in names:query(name, argv, env)
    try:
        if names is not None and 'user-services.txt' not in names:raise StopIteration
        from .runtime_support import UserSystemd
        bus = UserSystemd.connect()
        query('user-services.txt', ['/usr/bin/systemctl', '--user', '--no-pager', '--no-legend', '--plain', 'list-units', '--all', '--type=service'], bus.environment())
    except StopIteration:pass
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        (root / 'user-services.txt').write_text('')
        facts['user-services.txt'] = {'exit_code': None, 'error': str(exc), 'captured_at': time.time(), 'read_only': True}
    if 'wifi.txt' in facts:
        facts['wifi.txt']['semantics'] = 'NetworkManager cached access points; --rescan no; no active scan requested. Empty output alone does not prove absence of hardware or networks. Blank SSID means hidden name.'
        facts['wifi.txt']['columns'] = ['SSID', 'SIGNAL', 'SECURITY', 'IN-USE', 'BSSID']
    if 'network-devices.txt' in facts:facts['network-devices.txt']['columns'] = ['DEVICE', 'TYPE', 'STATE']
    (root / 'sources.json').write_text(json.dumps(facts, ensure_ascii=False))
    return facts
