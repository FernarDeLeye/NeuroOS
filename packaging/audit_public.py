"""Scoped publication checks. Findings never echo matched credential values.
Not a proof that arbitrary private information or every secret format is absent.
"""
import argparse,ast,hashlib,ipaddress,json,re,subprocess,sys
from pathlib import Path
PATTERNS={
 'private_key':re.compile(r'^-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',re.M),
 'github_token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
 'aws_key':re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
 'openai_key':re.compile(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b'),
 'developer_home':re.compile(r'/home/[A-Za-z0-9_.-]+(?:/|\b)'),
 'developer_checkout':re.compile(r'/opt/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'),
 'credentials_url':re.compile(r'https?://[^\s/:@]+:[^\s/@]+@'),
}
FORBIDDEN={'.sqlite','.db','.gguf','.jsonl','.pem','.key','.pyc','.deb','.buildinfo','.changes'}
def inspect_bytes(data,label):
 result=[]
 if data[:16]==b'SQLite format 3\x00' or data[:4] in {b'GGUF',b'\x7fELF'}:result.append({'path':label,'rule':'binary_state_or_executable'})
 try:text=data.decode('utf-8')
 except UnicodeDecodeError:return result
 for rule,pattern in PATTERNS.items():
  for match in pattern.finditer(text):result.append({'path':label,'rule':rule,'line':text.count('\n',0,match.start())+1,'match_sha256':hashlib.sha256(match.group().encode()).hexdigest()})
 for match in re.finditer(r'\b(?:\d{1,3}\.){3}\d{1,3}\b',text):
  try:addr=ipaddress.ip_address(match.group())
  except ValueError:continue
  if not addr.is_loopback and not addr.is_unspecified:result.append({'path':label,'rule':'nonloopback_address','line':text.count('\n',0,match.start())+1,'match_sha256':hashlib.sha256(match.group().encode()).hexdigest()})
 return result
def audit(root,history=True):
 root=Path(root).resolve();findings=[];manifest=[];imports=set()
 for p in sorted(root.rglob('*')):
  rel=p.relative_to(root)
  if any(x in {'.git','__pycache__'} for x in rel.parts):continue
  if p.is_symlink():findings.append({'path':str(rel),'rule':'symlink'});continue
  if not p.is_file():continue
  data=p.read_bytes();manifest.append({'path':str(rel),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
  if p.suffix in FORBIDDEN or p.name.startswith('.env') or any(x in {'research','evaluation','operations','state','.aws','.ssh','.codex'} for x in rel.parts):findings.append({'path':str(rel),'rule':'forbidden_payload'})
  findings.extend(inspect_bytes(data,str(rel)))
  if p.suffix=='.py':
   for n in ast.walk(ast.parse(data)):
    if isinstance(n,ast.Import):imports.update(x.name.split('.')[0] for x in n.names)
    elif isinstance(n,ast.ImportFrom) and n.level==0 and n.module:imports.add(n.module.split('.')[0])
 info={'commits':None,'blob_count':0,'remote_count':0}
 if history and (root/'.git').exists():
  def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
  info['commits']=int(git('rev-list','--count','--all'));info['remote_count']=len(git('remote').splitlines())
  if info['remote_count']:findings.append({'path':'.git/config','rule':'unexpected_remote'})
  seen=set()
  for line in git('rev-list','--objects','--all').decode().splitlines():
   oid=line.split(' ',1)[0]
   if oid in seen:continue
   seen.add(oid)
   if git('cat-file','-t',oid).strip()!=b'blob':continue
   data=git('cat-file','blob',oid);info['blob_count']+=1;findings.extend(inspect_bytes(data,'git-blob:'+oid))
 local={p.stem for p in (root/'neuro_next').glob('*.py')}|{'neuro_next'}
 third_party=sorted(imports-sys.stdlib_module_names-local)
 if third_party:findings.append({'path':'Python imports','rule':'review_nonstdlib','modules':third_party})
 return {'pass':not findings,'scope':'release worktree plus all reachable Git blobs when initialized; heuristic, not formal certification','history':info,'findings':findings,'files':len(manifest),'manifest':manifest,'python_nonstdlib_imports':third_party,'attribution_pending':'[TO BE PROVIDED BY THE PROJECT OWNER]' in (root/'NOTICE').read_text()}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('root',nargs='?',default='.');p.add_argument('--output');a=p.parse_args();r=audit(a.root)
 body=json.dumps(r,indent=2)+'\n'
 if a.output:Path(a.output).write_text(body)
 else:print(body)
 raise SystemExit(0 if r['pass'] else 1)
