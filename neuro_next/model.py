"""Backend boundary: structured action + raw exchange, no cloud dependencies."""
import ipaddress
import json
from typing import Protocol
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SCHEMA = {'type': 'object', 'properties': {
    'kind': {'type': 'string', 'enum': ['shell', 'recall', 'finish']},
    'command': {'type': 'string'}, 'cwd': {'type': 'string'},
    'stdin': {'type': 'string'}, 'env': {'type': 'object', 'additionalProperties': {'type': 'string'}},
    'event_id': {'type': 'integer'}, 'offset': {'type': 'integer'},
    'status': {'type': 'string', 'enum': ['claimed', 'incomplete', 'blocked']},
    'report': {'type': 'string'}, 'memory': {'type': 'string'}},
    'required': ['kind'], 'additionalProperties': False}


class ModelBackend(Protocol):
    def generate(self, messages: list[dict]) -> dict: ...


def validate(action):
    if not isinstance(action, dict):
        raise ValueError('action must be object')
    kind = action.get('kind')
    keys = {'shell': {'kind', 'command', 'cwd', 'stdin', 'env', 'memory'},
            'recall': {'kind', 'event_id', 'offset', 'memory'},
            'finish': {'kind', 'status', 'report', 'memory'}}
    if kind not in keys or set(action) - keys[kind]:
        raise ValueError('unknown action or extra fields')
    for name in ['command', 'cwd', 'stdin', 'memory', 'report', 'status']:
        if name in action and (not isinstance(action[name], str) or len(action[name]) > 8192):
            raise ValueError('invalid string: ' + name)
    if len(action.get('memory', '')) > 1500:
        raise ValueError('memory exceeds 1500 characters')
    if kind == 'shell':
        if not action.get('command') or '\x00' in action['command']:
            raise ValueError('command required')
        env = action.get('env', {})
        if not isinstance(env, dict) or len(env) > 16:
            raise ValueError('invalid env')
        import re
        for k, v in env.items():
            if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', k) or not isinstance(v, str) or '\x00' in v or len(v) > 4096:
                raise ValueError('invalid env entry')
    if kind == 'recall':
        if type(action.get('event_id')) is not int or type(action.get('offset', 0)) is not int or action.get('offset', 0) < 0:
            raise ValueError('invalid recall range')
    if kind == 'finish' and (action.get('status') not in {'claimed', 'incomplete', 'blocked'} or not action.get('report')):
        raise ValueError('finish needs status and report')
    return action


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('backend redirects forbidden')


class LlamaCpp:
    def __init__(self, endpoint='http://127.0.0.1:8080', model='local', max_tokens=512,
                 thinking=False, timeout=600, seed=42):
        url = urlparse(endpoint)
        if url.scheme != 'http' or not url.hostname or not ipaddress.ip_address(url.hostname).is_loopback or url.username or url.query or url.fragment:
            raise ValueError('backend must be explicit loopback HTTP address')
        self.endpoint, self.model = endpoint.rstrip('/'), model
        self.max_tokens, self.thinking, self.timeout, self.seed = max_tokens, thinking, timeout, seed
        self.http = build_opener(ProxyHandler({}), NoRedirect())

    def generate(self, messages):
        request = {'model': self.model, 'messages': messages, 'temperature': 0.1,
                   'seed': self.seed, 'max_tokens': self.max_tokens, 'stream': False,
                   'chat_template_kwargs': {'enable_thinking': self.thinking},
                   'response_format': {'type': 'json_object', 'schema': SCHEMA}}
        raw = None
        result = None
        try:
            req = Request(self.endpoint + '/v1/chat/completions', json.dumps(request).encode(),
                          {'Content-Type': 'application/json'})
            with self.http.open(req, timeout=self.timeout) as response:
                raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise ValueError('backend response too large')
            result = json.loads(raw)
            choice = result['choices'][0]
            if choice['finish_reason'] != 'stop':
                raise ValueError('generation did not finish: ' + str(choice['finish_reason']))
            action = validate(json.loads(choice['message']['content']))
            return {'action': action, 'request': request, 'response': result, 'usage': result.get('usage')}
        except Exception as exc:
            return {'error': str(exc), 'request': request,
                    'raw_response': raw.decode(errors='replace') if raw else None,
                    'response': result, 'usage': result.get('usage') if isinstance(result, dict) else None}
