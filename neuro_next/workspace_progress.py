"""Bounded state progress, independent of presentation of tool observations."""
import hashlib
import json
from .workspace_tree import metadata


class StateProgress:
    def __init__(self, scratch, live, limit=3, *, normalizer=None, extra_state=None):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError('bounded no-progress limit required')
        self.normalizer = normalizer
        self.extra_state = extra_state
        self.limit = limit
        self.stagnant = 0
        self.knowledge_seen = set()
        self.previous_raw = self.digest(scratch, live)
        self.previous = self.key(scratch, live)
        self.seen = {self.previous}

    @staticmethod
    def digest(scratch, live):
        # Trusted snapshots contain content hashes, ordinary modes, names and
        # symlink targets, not timestamps/inodes/export UUIDs. Do not rewrite
        # raw evidence or interpret arbitrary stdout as a filesystem mutation.
        raw = json.dumps({'scratch': metadata(scratch), 'live': metadata(live)}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def key(self,scratch,live):
        def useful(tree):return metadata({p:v for p,v in tree.items() if p!='.tmp' and not p.startswith('.tmp/')})
        normalize=self.normalizer or useful
        state={'scratch':normalize(scratch),'live':normalize(live)}
        if self.extra_state is not None:state['supervised_processes']=self.extra_state()
        raw=json.dumps(state,sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def observe(self, scratch, live, observation, *, file_output=False, maintenance=False,host_output=False):
        raw_current = self.digest(scratch, live)
        current = self.key(scratch, live)
        novel = current not in self.seen
        fact=None;useful=False
        dataset=observation.get('host_dataset',{})
        if host_output and observation.get('exit_code')==0 and dataset.get('available'):
            candidate=hashlib.sha256(json.dumps({'source':dataset['source'],'rows':dataset['rows']},sort_keys=True).encode()).hexdigest()
            if candidate not in self.knowledge_seen and len(self.knowledge_seen)<min(3,self.limit):
                self.knowledge_seen.add(candidate);fact=candidate;useful=True
        if file_output and observation.get('exit_code')==0 and isinstance(observation.get('stdout'),str) and observation['stdout']:
            raw=observation['stdout'].encode();candidate=hashlib.sha256(raw).hexdigest()
            matches=any(v.get('kind')=='file' and v.get('size')==len(raw) and v.get('sha256')==candidate for p,v in scratch.items() if p!='.tmp' and not p.startswith('.tmp/'))
            if matches and candidate not in self.knowledge_seen and len(self.knowledge_seen)<min(3,self.limit):
                self.knowledge_seen.add(candidate);fact=candidate;useful=True
        self.stagnant = 0 if novel or useful else self.stagnant if maintenance else self.stagnant + 1
        result = {'state_changed': raw_current != self.previous_raw, 'new_state': novel,'useful_information':useful,'knowledge_fact_sha256':fact,'information_credits_used':len(self.knowledge_seen),'controller_maintenance':maintenance,
                  'stagnant_actions': self.stagnant, 'limit': self.limit,
                  'stop': self.stagnant >= self.limit, 'state_sha256': current,
                  'raw_state_sha256':raw_current,'state_basis':('contract' if self.normalizer else 'filesystem-content-and-mode')+('+supervised-processes' if self.extra_state else '')}
        self.seen.add(current)
        self.previous_raw = raw_current
        self.previous = current
        return result
