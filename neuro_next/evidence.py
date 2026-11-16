"""Task-scoped evidence roots and bounded progress, independent of task families."""
import hashlib
import json

ROOT_KINDS = {'observation': 'tool_execution', 'saved_observation': 'saved_observation'}


def record_saved_observation(store, task, observation, origin):
    """Trusted application import, never an action exposed to the model.

    A saved fact retains its operator/source reference. Importing a fact does
    not certify that it still describes current system state; the verifier's
    task contract must decide whether saved evidence is suitable.
    """
    store.get(task)
    if not isinstance(observation, dict) or not isinstance(origin, dict):
        raise ValueError('saved observation requires data and source')
    if any(not isinstance(origin.get(k), str) or not origin[k] for k in ['source', 'reference']):
        raise ValueError('saved observation requires source/reference')
    data = {'observation': observation, 'origin': origin}
    if len(json.dumps(data)) > 65536:
        raise ValueError('saved observation is too large')
    return store.event(task, 'saved_observation', data)


def resolve_evidence(store, task, event_id, offset=0):
    if type(event_id) is not int or event_id < 1 or type(offset) is not int or offset < 0:
        raise ValueError('invalid evidence reference/range')
    requested, visited = event_id, set()
    while True:
        if event_id in visited or len(visited) >= 64:
            raise ValueError('cyclic or excessive evidence alias chain')
        visited.add(event_id)
        event = store.get_event(task, event_id)
        if event is None:
            raise ValueError('evidence is missing, unavailable or belongs to another task')
        if event['kind'] in ROOT_KINDS:
            if event['kind'] == 'saved_observation':
                data = event['data']
                if (not isinstance(data.get('observation'), dict) or not isinstance(data.get('origin'), dict)
                        or any(not isinstance(data['origin'].get(k), str) or not data['origin'][k]
                               for k in ['source', 'reference'])):
                    raise ValueError('saved observation has no original source reference')
            break
        if event['kind'] != 'recall':
            raise ValueError('event is not an accessible observation')
        # Recompute from the root. Never trust the cached alias page as a fact.
        event_id = event['data'].get('root_event_id', event['data'].get('source_id'))
        if type(event_id) is not int or event_id < 1:
            raise ValueError('recall alias has no valid origin')
    payload = {'observation': event['data'], 'origin_type': ROOT_KINDS[event['kind']]}
    if event['kind'] == 'observation' and event['data'].get('intent_id'):
        intent = store.get_event(task, event['data']['intent_id'])
        if intent is None or intent['kind'] != 'intent':
            raise ValueError('tool observation has an invalid intent origin')
        payload['action'] = intent['data']
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if offset >= len(raw):
        raise ValueError('evidence offset exceeds original observation')
    return {'source_id': event_id, 'root_event_id': event_id, 'requested_id': requested,
            'origin_type': ROOT_KINDS[event['kind']], 'task': task, 'offset': offset,
            'total_chars': len(raw), 'page': raw[offset:offset + 1200],
            'fact_sha256': hashlib.sha256(raw.encode()).hexdigest()}


def evidence_catalog(store, task):
    result = []
    for event in store.events(task):
        if event['kind'] not in ROOT_KINDS:
            continue
        fact = resolve_evidence(store, task, event['id'])
        result.append({k: fact[k] for k in ['root_event_id', 'origin_type', 'fact_sha256', 'total_chars']})
    return result


class EvidenceProgress:
    """Aliases and new model notes do not reset a fact-consumption budget.

    New pages of a real observation may be read. Overlapping pages only count
    for the newly exposed interval. Tool changes are managed by the shell guard.
    """
    def __init__(self, limit=3):
        self.limit = limit
        self.ranges = {}
        self.stagnant = 0
    def recall(self, fact):
        key = (fact['root_event_id'], fact['fact_sha256'])
        start, end = fact['offset'], fact['offset'] + len(fact['page'])
        intervals = self.ranges.setdefault(key, [])
        covered = sum(max(0, min(end, b) - max(start, a)) for a, b in intervals)
        novel = covered < end - start
        points = sorted(intervals + [(start, end)])
        merged = []
        for a, b in points:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        self.ranges[key] = merged
        self.stagnant = 0 if novel else self.stagnant + 1
        return novel, self.stagnant >= self.limit
    def tool_progress(self):
        self.stagnant = 0


def evidence_class(event):
    return {'observation': 'tool_execution', 'saved_observation': 'saved_observation',
            'recall': 'evidence_reference', 'model_inference': 'model_inference',
            'assumption': 'unconfirmed_assumption', 'model': 'model_output',
            'claim': 'unverified_model_claim', 'external_verification': 'independent_verification'
            }.get(event['kind'], 'controller_metadata')
