import importlib,json,os,sqlite3,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from neuro_next import installation as inst
from neuro_next.distribution_cli import feedback,gguf
class Installation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.addCleanup(patch.stopall)
  patch.dict(os.environ,{'NEUROOS_CONFIG':str(self.root/'config.json'),'XDG_STATE_HOME':str(self.root/'state')}).start()
 def config(self):
  return {'version':1,'backend':'external','endpoint':'http://127.0.0.1:8888','model_id':'test-local','model_path':str(self.root/'weights.gguf'),'llama_server':'','data_dir':str(self.root/'data'),'memory_max_mib':4096,'threads':2,'profile':{'context':4096,'max_tokens':1024,'thinking':False,'request_timeout_s':60}}
 def test_configuration_local_only_and_private(self):
  c=self.config();inst.save(c);self.assertEqual(inst.load(),c);self.assertEqual(inst.config_path().stat().st_mode&0o777,0o600)
  for endpoint in ['http://example.org:8080','http://127.0.0.1:8080/path','http://user@127.0.0.1:8080']:
   c['endpoint']=endpoint
   with self.assertRaises(ValueError):inst.validate(c)
 def test_feedback_does_not_collect_contents_or_identifiers(self):
  c=self.config();inst.save(c);data=Path(c['data_dir']);data.mkdir();db=sqlite3.connect(data/'state.sqlite');db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,task,kind,data)');db.execute('INSERT INTO events(task,kind,data) VALUES(?,?,?)',('a'*16,'model',json.dumps({'request':{'messages':['password SECRET SSID private-key']},'response':{'usage':{'prompt_tokens':5}},'duration_s':2})));db.commit();db.close()
  out=self.root/'feedback.tar.gz';feedback(out,'a'*16)
  with tarfile.open(out) as t:body=b''.join(t.extractfile(n).read() for n in t.getnames());self.assertNotIn(b'SECRET',body);self.assertNotIn(str(self.root).encode(),body);self.assertIn(b'prompt_tokens',body)
  with self.assertRaises(FileExistsError):feedback(out)
 def test_gguf_checks_only_header_not_a_claim_of_real_inference(self):
  path=self.root/'weights.gguf';path.write_bytes(b'GGUF'+(3).to_bytes(4,'little')+b'\0'*16);self.assertEqual(gguf(path)['gguf_version'],3)
  path.write_bytes(b'not a model')
  with self.assertRaises(ValueError):gguf(path)
 def test_portable_image_has_no_research_dependency_or_state(self):
  subprocess.run([sys.executable,'packaging/install.py',str(self.root/'image')],check=True)
  app=self.root/'image/usr/lib/neuroos';files=list(app.rglob('*'))
  self.assertFalse(any(p.suffix in {'.sqlite','.gguf','.so'} for p in files))
  for p in app.rglob('*.py'):
   self.assertNotIn('/opt/neuroos',p.read_text());self.assertNotIn('from operations.',p.read_text());self.assertNotIn('from evaluation.',p.read_text())
  r=subprocess.run([sys.executable,'-c',"from neuro_next.model_adapter import ModelAdapter; from neuro_next.grammar import GrammarCompiler; assert GrammarCompiler().mode=='server-response-format'"],cwd=app,capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr)
 def test_store_additive_migration_preserves_old_tasks(self):
  from neuro_next.store import Store
  data=self.root/'legacy';data.mkdir();db=sqlite3.connect(data/'state.sqlite');db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,goal TEXT,status TEXT,memory TEXT,report TEXT,created REAL)');db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('a'*16,'private goal','paused','','',1));db.commit();db.close()
  s=Store(data);self.assertEqual(s.get('a'*16)['goal'],'private goal');self.assertEqual(s.get('a'*16)['status'],'paused');self.assertEqual(s.db.execute('PRAGMA integrity_check').fetchone()[0],'ok');s.db.close()
 def test_installation_migration_backups_and_future_schema_refusal(self):
  c=self.config();inst.save(c);root=Path(c['data_dir']);root.mkdir();root.chmod(0o700)
  db=sqlite3.connect(root/'state.sqlite');db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,goal TEXT,status TEXT,memory TEXT,report TEXT,created REAL)');db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('a'*16,'retained','paused','','',1));db.commit();db.close()
  inst.prepare_data();self.assertEqual(len(list((root/'backups').glob('*.sqlite'))),1)
  inst.prepare_data();self.assertEqual(len(list((root/'backups').glob('*.sqlite'))),1)
  db=sqlite3.connect(root/'state.sqlite');self.assertEqual(db.execute('SELECT goal FROM tasks').fetchone()[0],'retained');db.execute('PRAGMA user_version=99');db.close()
  with self.assertRaises(ValueError):inst.prepare_data()
