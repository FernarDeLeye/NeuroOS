import json
from pathlib import Path
import sqlite3
import time
import uuid


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL,
          profile TEXT NOT NULL DEFAULT 'workspace-v1',
          memory TEXT NOT NULL DEFAULT '', report TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, task TEXT NOT NULL, kind TEXT NOT NULL,
          data TEXT NOT NULL, created REAL NOT NULL);
        ''')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS task_workspaces(task TEXT PRIMARY KEY,conversation TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_specs(task TEXT PRIMARY KEY,spec TEXT NOT NULL,options TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS task_runtime(task TEXT PRIMARY KEY,control TEXT,stage TEXT NOT NULL DEFAULT 'READY',checkpoint TEXT NOT NULL DEFAULT '{}',run TEXT);
        ''')
        workspace_columns={row[1] for row in self.db.execute('PRAGMA table_info(task_workspaces)')}
        for name in ['published_path','identity']:
            if name not in workspace_columns:self.db.execute('ALTER TABLE task_workspaces ADD COLUMN '+name+' TEXT')
        self.db.commit()
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(tasks)')}
        if 'profile' not in columns:
            self.db.execute("ALTER TABLE tasks ADD COLUMN profile TEXT NOT NULL DEFAULT 'workspace-v1'")
            self.db.commit()

    def create(self, goal):
        if not goal or len(goal) > 4000:
            raise ValueError('goal must contain 1..4000 characters')
        task = uuid.uuid4().hex[:16]
        self.db.execute('INSERT INTO tasks(id,goal,status,created) VALUES(?,?,?,?)', (task, goal, 'ready', time.time()))
        self.db.commit()
        self.workspace(task).mkdir(parents=True, mode=0o700)
        return task

    def get(self, task):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
        if row is None:
            raise ValueError('unknown task')
        return dict(row)

    def workspace(self, task):
        if len(task) != 16 or any(c not in '0123456789abcdef' for c in task):
            raise ValueError('invalid task id')
        row=self.db.execute('SELECT conversation,published_path,identity FROM task_workspaces WHERE task=?',(task,)).fetchone()
        if row and row['published_path']:
            import os,stat
            target=Path(row['published_path']);info=target.lstat()
            if not stat.S_ISDIR(info.st_mode) or str(target.resolve())!=str(target) or [info.st_dev,info.st_ino,info.st_uid]!=json.loads(row['identity']) or info.st_uid!=os.getuid() or info.st_mode&0o022:raise ValueError('Authorized workspace identity changed; no silent adoption')
            return target
        if row:
            conversation=row['conversation']
            if len(conversation)!=16 or any(c not in '0123456789abcdef' for c in conversation):raise ValueError('invalid conversation workspace')
            return self.root/'conversations'/conversation/'work'
        return self.root / task / 'work'

    def update(self, task, **values):
        if not values or set(values) - {'status', 'memory', 'report'}:
            raise ValueError('invalid task fields')
        self.db.execute('UPDATE tasks SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', (*values.values(), task))
        self.db.commit()

    def event(self, task, kind, data):
        cursor = self.db.execute('INSERT INTO events(task,kind,data,created) VALUES(?,?,?,?)',
                                 (task, kind, json.dumps(data, ensure_ascii=False), time.time()))
        self.db.commit()
        return cursor.lastrowid

    def events(self, task):
        return [dict(r) | {'data': json.loads(r['data'])} for r in self.db.execute(
            'SELECT * FROM events WHERE task=? ORDER BY id', (task,))]

    def recent_context_events(self, task, limit=8):
        rows = self.db.execute(
            "SELECT * FROM events WHERE task=? AND kind!='model' ORDER BY id DESC LIMIT ?", (task, limit))
        return [dict(r) | {'data': json.loads(r['data'])} for r in rows]

    def get_event(self, task, event_id):
        row = self.db.execute("SELECT * FROM events WHERE task=? AND id=? AND kind!='model'", (task, event_id)).fetchone()
        return dict(row) | {'data': json.loads(row['data'])} if row else None

    def register(self,task,spec,options):
        self.get(task)
        with self.db:
            self.db.execute('INSERT INTO task_specs VALUES(?,?,?,?)',(task,json.dumps(spec),json.dumps(options),time.time()))
            self.db.execute('INSERT INTO task_runtime(task) VALUES(?)',(task,))

    def specification(self,task):
        row=self.db.execute('SELECT * FROM task_specs WHERE task=?',(task,)).fetchone()
        return dict(row)|{'spec':json.loads(row['spec']),'options':json.loads(row['options'])} if row else None

    def runtime(self,task):
        self.get(task);row=self.db.execute('SELECT * FROM task_runtime WHERE task=?',(task,)).fetchone()
        return dict(row)|{'checkpoint':json.loads(row['checkpoint']),'run':json.loads(row['run']) if row['run'] else None} if row else None

    def runtime_update(self,task,**values):
        if not values or set(values)-{'control','stage','checkpoint','run'}:raise ValueError('invalid runtime fields')
        for key in ['checkpoint','run']:
            if key in values:values[key]=json.dumps(values[key]) if values[key] is not None else None
        with self.db:self.db.execute('UPDATE task_runtime SET '+','.join(k+'=?' for k in values)+' WHERE task=?',(*values.values(),task))

    def transition(self,task,status,stage,report,kind='task_transition',data=None):
        self.get(task)
        with self.db:
            self.db.execute('UPDATE tasks SET status=?,report=? WHERE id=?',(status,report,task))
            self.db.execute('UPDATE task_runtime SET stage=? WHERE task=?',(stage,task))
            self.db.execute('INSERT INTO events(task,kind,data,created) VALUES(?,?,?,?)',(task,kind,json.dumps({'status':status,'stage':stage,'report':report,**(data or {})}),time.time()))
