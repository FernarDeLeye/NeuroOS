"""User CLI: durable tasks, existing rootless execution, local model only."""
import argparse,contextlib,fcntl,hashlib,json,os,signal,subprocess,sys,threading,time
from pathlib import Path
from .store import Store
from .task_lifecycle import TaskLifecycle
from .workspace_tree import snapshot,materialize,metadata,logical_path
from .publication import PublicationPolicy,Publisher
from .workspace_agent import WorkspaceKernel,WorkspaceBackend,WorkspaceContract
ROOT=Path(__file__).resolve().parents[1]
DEFAULT=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'neuroos-next'


def open_store(root):
    if os.geteuid()==0:raise ValueError('NeuroOS task CLI runs as the ordinary user, not root')
    st=Store(root)
    if st.root.stat().st_uid!=os.getuid() or st.root.stat().st_mode&0o077:raise ValueError('persistent state must be a private owned directory (0700)')
    return st


def submit(store,goal,input_dir=None,spec=None,options=None):
    spec=spec or {'policy':{},'conditions':None}
    if set(spec)!={'policy','conditions'}:raise ValueError('contract requires policy and conditions')
    PublicationPolicy(**spec['policy'])
    if spec['conditions'] is not None:WorkspaceContract(spec['conditions'])
    tree=snapshot(Path(input_dir).resolve()) if input_dir else {}
    from .model_adapter import ModelAdapter
    adapter=ModelAdapter()
    opts={'steps':12,'seconds':1800,'shell_seconds':15,'model_id':adapter.config['default_agent_model'],'model_profile':adapter.profile(adapter.config['default_agent_model']),**(options or {})}
    if not 1<=opts['steps']<=32 or not 30<=opts['seconds']<=3600 or not 1<=opts['shell_seconds']<=600:raise ValueError('bounded task/process budgets required')
    if opts.get('test_stop_at') not in {None,'after_execution_receipt','publication_file_committed'}:raise ValueError('unknown engineering checkpoint')
    task=store.create(goal)
    try:materialize(tree,store.workspace(task));store.register(task,spec,opts)
    except BaseException:store.update(task,status='blocked',report='Task input/specification preparation failed; not executable.');raise
    store.event(task,'submitted',{'input_source':str(Path(input_dir).resolve()) if input_dir else None,'input_snapshot':metadata(tree),'permissions':spec['policy'],'independent_contract_present':spec['conditions'] is not None,'options':opts})
    return task


def unit(task):return 'neuroos-task-'+task+'.service'


def unit_state(task):
    from .runtime_support import UserSystemd
    bus=UserSystemd.connect();r=subprocess.run(['systemctl','--user','show',unit(task),'-p','ActiveState','-p','LoadState','-p','MainPID'],env=bus.environment(),capture_output=True,text=True,timeout=5)
    return dict(l.split('=',1) for l in r.stdout.splitlines() if '=' in l)


def launch(store,task,*,slice_actions=None):
    from .runtime_support import UserSystemd
    import shlex
    store.workspace(task);store.get(task)
    if any(c in str(store.root) for c in '\n\r%$'):raise ValueError('state path contains unsupported systemd substitutions/control characters')
    if slice_actions is not None and not 1<=slice_actions<=32:raise ValueError('slice actions must be 1..32')
    if not store.specification(task):raise ValueError('not a persistent task')
    if store.get(task)['status'] in {'verified','cancelled'} or (store.specification(task)['options'].get('process_action') and store.get(task)['status'] in {'unverified','failed'}):return {'task':task,'status':store.get(task)['status'],'started':False}
    bus=UserSystemd.connect();env=bus.environment()
    for _ in range(50):
        props=unit_state(task)
        if props.get('ActiveState') in {'active','activating','reloading'}:return {'task':task,'status':'controller_already_running','unit':unit(task),'started':False}
        if props.get('LoadState')=='not-found' or not props.get('LoadState'):break
        time.sleep(.1)
    argv=[sys.executable,'-m','neuro_next.persistent','--state',str(store.root),'worker',task]
    if slice_actions:argv+=['--slice-actions',str(slice_actions)]
    finalizer=[sys.executable,'-m','neuro_next.persistent','--state',str(store.root),'finalize',task]
    seconds=store.specification(task)['options']['seconds']
    command=['systemd-run','--user','--quiet','--collect','--unit='+unit(task),'--working-directory='+str(ROOT),'-p','Type=exec','-p','KillMode=control-group','-p','NoNewPrivileges=yes','-p','TasksMax=64','-p','MemoryMax=512M','-p','MemorySwapMax=0','-p','RuntimeMaxSec='+str(seconds+120)+'s','-p','TimeoutStopSec=10s','-p','ExecStopPost='+shlex.join(finalizer),*argv]
    from .runtime_support import unit_environment_args
    command[1:1]=unit_environment_args()
    subprocess.run(command,env=env,capture_output=True,text=True,timeout=10,check=True)
    return {'task':task,'status':'STARTED','unit':unit(task),'state':str(store.root),'result_directory':str(store.workspace(task))}


class ControlWatcher:
    def __init__(self,root,task,cancel,stop):self.root,self.task,self.cancel,self.stop=root,task,cancel,stop;self.signal=threading.Event()
    def __enter__(self):
        def watch():
            st=Store(self.root)
            try:
                while not self.stop.wait(.1):
                    if self.signal.is_set():st.runtime_update(self.task,control='pause');self.signal.clear()
                    owner=st.specification(self.task)['options'].get('owner_parent')
                    if owner and st.get(owner)['status']=='cancelled':st.runtime_update(self.task,control='cancel')
                    state=st.runtime(self.task)
                    if state['control']=='cancel' or state['control']=='pause' and state['stage']=='INFERENCE':self.cancel.set()
            finally:st.db.close()
        self.thread=threading.Thread(target=watch,daemon=True);self.thread.start();return self
    def __exit__(self,*args):self.stop.set();self.thread.join(timeout=3)


def conversational_transport_timeout(profile,options):
    # A cold 9B prompt measured ~766s before any answer tokens. Preserve the
    # registered model profile; only HTTP waiting uses more of the existing task
    # allocation. completion_request still enforces the remaining task deadline.
    return max(profile['request_timeout_s'],min(1200,options['seconds'])) if options.get('conversation') else profile['request_timeout_s']


def worker(store,task,slice_actions=None,*,reconcile_only=False,discard_unconfirmed=False):
    from .model_adapter import ModelAdapter
    from .runtime_support import Resources
    adapter=ModelAdapter();model=adapter.config['default_agent_model'];profile=adapter.profile(model)
    from .installation import installed
    if not installed() and (profile['context']!=8192 or profile['max_tokens']!=2048 or profile['thinking'] is not False):raise ValueError('persistent baseline requires qualified 8192/2048 non-thinking profile')
    registered=store.specification(task)
    if not registered:raise ValueError('persistent specification absent')
    spec,opts=registered['spec'],registered['options']
    if opts.get('model_id',model)!=model or opts.get('model_profile',profile)!=profile:raise ValueError('task model/profile differs from its registered baseline; explicit migration required')
    chat=None
    if opts.get('conversation'):
        from .chat import ChatRuntime
        chat=ChatRuntime(store,task)
    life=TaskLifecycle(store,task,slice_actions=slice_actions,reconcile_only=reconcile_only,discard_unconfirmed=discard_unconfirmed)
    class LocalBackend(WorkspaceBackend):
        selected=False
        def generate(self,messages):
            if not self.selected:
                ready=adapter.ensure_ready(deadline=self.deadline)
                if ready['status']!='READY':raise RuntimeError('Default model unavailable/DEGRADED; no silent model substitution')
                store.event(task,'model_selection',ready|{'inference_profile':profile,'effective_transport_timeout_s':self.timeout});self.selected=True
            return super().generate(messages)
    backend=LocalBackend(endpoint=adapter.config['endpoint'],model=model,max_tokens=profile['max_tokens'],thinking=profile['thinking'],timeout=conversational_transport_timeout(profile,opts))
    backend.configure_context_budget(profile['context']);backend.request_recorder=lambda request:store.event(task,'inference_request',{'request':request})
    cancel=threading.Event();stop=threading.Event();output=store.root/(task+'.metrics')/str(time.time_ns());output.mkdir(parents=True,mode=0o700)
    with ControlWatcher(store.root,task,cancel,stop) as controls:
        prior={}
        for sig in [signal.SIGTERM,signal.SIGHUP,signal.SIGINT]:prior[sig]=signal.signal(sig,lambda *args:controls.signal.set())
        try:
            with (chat.chat.lock(chat.id) if chat else contextlib.nullcontext()) as chat_lock:
                if chat:fcntl.flock(chat_lock,fcntl.LOCK_EX)
                with Resources(output,cancel,model=model) as monitor:
                    result=process_worker(store,task,life,cancel) if opts.get('process_action') else WorkspaceKernel(store,backend,policy=PublicationPolicy(**spec['policy']),contract=WorkspaceContract(spec['conditions']) if spec['conditions'] else None,steps=opts['steps'],shell_timeout=opts['shell_seconds'],max_repairs=2,no_progress_limit=3,cancel_event=cancel,autonomous_completion=True,conversation_context=True,lifecycle=life,chat=chat).run(task)
                result['resource_abort']=monitor.reason
                if chat:
                    if result.get('status')=='cancelled' or result.get('execution_status') in {'TIME_BUDGET','RESOURCE_ABORT'}:chat.processes.stop_owned_by_parent()
                    chat.chat.finish(task)
        finally:
            for sig,handler in prior.items():signal.signal(sig,handler)
    from .durable import save
    save(store.root/(task+'.result.json'),result);return result


def process_worker(store,task,life,cancel):
    from .execution_workspace import ExecutionWorkspace
    from .task_lifecycle import TaskStop
    from .task_completion import PreparedCompletion
    from types import SimpleNamespace
    spec=store.specification(task)['spec'];opts=life.options;pub=Publisher(store.workspace(task),store.root/(task+'.publication'),PublicationPolicy(**spec['policy']))
    def finish(status,execution,verification,report):
        from .completion import CompletionKernel
        CompletionKernel._state(SimpleNamespace(store=store),task,status,execution,verification,report)
        store.runtime_update(task,stage=verification if status=='verified' else status.upper())
    with open(store.root/(task+'.lock'),'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            life.ensure_scratch(store.root/(task+'.execution'));work=ExecutionWorkspace(store.root/(task+'.execution'),store.workspace(task));baseline,seconds=life.prepare(work,pub)
            pub.persistent_lifecycle=True;pub.checkpoint=lambda point,record:life.hook(point)
            life.stop_requested()
            if life.reconcile_only:raise TaskStop('paused','Process reconciled; command is not replayed.')
            store.update(task,status='running');life.stage('EXECUTION')
            events=store.events(task);intents=[e for e in events if e['kind']=='intent']
            if not intents:
                intent=life.intent(opts['process_action']);pub.operation_id=intent
                observation=work.execute(opts['process_action'],timeout=min(seconds,opts['shell_seconds']),operation_id=intent,cancel_event=cancel,pty_mode=opts.get('pty',False),persistent_stdin=opts.get('open_stdin',False))
                life.hook('after_execution_receipt');store.event(task,'observation',observation|{'intent_id':intent});life.hook('observation_recorded')
            else:
                intent=intents[-1]['id'];pub.operation_id=intent
                observation=next(e['data'] for e in store.events(task) if e['kind']=='observation' and e['data'].get('intent_id')==intent)
            life.stop_requested()
            if cancel.is_set():raise TaskStop('blocked','Resource guard stopped process; no further publication.')
            if observation.get('exit_code')!=0:
                finish('unverified','EXITED','UNVERIFIED','Process ended without successful exit; command is not retried.')
            elif spec['conditions']:
                contract=WorkspaceContract(spec['conditions']);protected={p:v for p,v in baseline.items() if v['kind']!='dir' and p not in pub.policy.edit and p not in pub.policy.remove}
                comp=PreparedCompletion(store,task,work,pub,contract,protected,2)
                if life.live_satisfied(contract,protected,comp):finish('verified','DONE','VERIFIED','Committed process result independently reconciled.')
                else:
                    transition=comp.check()
                    if transition and transition.get('verified'):finish('verified','DONE','VERIFIED','Process artifacts independently verified.')
                    else:finish('unverified','EXITED','UNVERIFIED','Process exited; goal postconditions not independently confirmed.')
            else:finish('unverified','EXITED','UNVERIFIED','Process exit captured; no independent goal contract supplied.')
        except TaskStop as exc:finish(exc.status,exc.status.upper(),'UNVERIFIED',exc.reason);store.runtime_update(task,control=None)
        except Exception as exc:
            store.event(task,'runtime_error',{'category':'process_controller','message':str(exc)});finish('uncertain','UNCERTAIN','UNVERIFIED','Process outcome requires reconciliation: '+str(exc))
        finally:life.finish_run()
    return result(store,task)


def send_input(store,task,text=None,eof=False,*,request_id=None):
    import stat
    state=unit_state(task)
    if state.get('ActiveState')!='active':raise ValueError('task controller is not active')
    events=store.events(task);intent=next((e for e in reversed(events) if e['kind']=='intent'),None)
    if not intent:raise ValueError('no active process')
    root=store.root/(task+'.execution');record=json.loads((root/('operation-'+str(intent['id'])+'.json')).read_text())
    if record['phase']!='RUNNING' or not (record.get('pty') or record.get('stdin_channel')):raise ValueError('no live stdin/PTY operation')
    payload=(json.dumps({'eof':True} if eof else {'text':text},ensure_ascii=False)+'\n').encode()
    if len(payload)>4096:raise ValueError('input message exceeds atomic FIFO bound')
    fifo=root/('operation-'+str(intent['id'])+'.stdin');fd=os.open(fifo,os.O_WRONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):raise ValueError('expected private process FIFO')
        os.write(fd,payload)
    finally:os.close(fd)
    store.event(task,'process_input',{'intent_id':intent['id'],'eof':eof,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),**({'request_id':request_id} if request_id else {})})
    return {'task':task,'queued':True,'eof':eof}


def finalize(store,task):
    from .chat import ChatStore
    life=TaskLifecycle(store,task);life.finish_run()
    if store.get(task)['status'] in {'running','checking','repairing'}:store.transition(task,'uncertain','UNCERTAIN','Controller ended without a final checkpoint; reconciliation required. No action replay.','controller_lost')
    ChatStore(store).finish(task)


def request(store,task,action):
    with store.db:
        row=store.get(task)
        if row['status'] in {'verified','cancelled'}:return {'task':task,'status':row['status'],'changed':False}
        store.db.execute('UPDATE task_runtime SET control=? WHERE task=?',(action,task))
        store.db.execute('INSERT INTO events(task,kind,data,created) VALUES(?,?,?,?)',(task,'operator',json.dumps({'action':action}),time.time()))
    return {'task':task,'control':action.upper()+'_REQUESTED','status':row['status']}


def status(store,task):
    row=store.get(task);runtime=store.runtime(task)
    if runtime is None:raise ValueError('not a persistent task')
    run=runtime['run'];controller_alive=None
    if run and run.get('active') and run.get('pid_namespace')==os.readlink('/proc/self/ns/pid'):
        try:controller_alive=Path('/proc/'+str(run['pid'])+'/stat').read_text().rsplit(') ',1)[1].split()[19]==run['pid_start']
        except FileNotFoundError:controller_alive=False
        if controller_alive is False:row=row|{'recorded_status':row['status'],'status':'uncertain'}
    events=store.events(task);intents=[e for e in events if e['kind']=='intent'];observations={e['data'].get('intent_id') for e in events if e['kind']=='observation'}
    return row|{'controller_alive':controller_alive,'stage':'UNCERTAIN' if controller_alive is False else runtime['stage'],'control':runtime['control'],'run':runtime['run'],'pending_intents':[e['id'] for e in intents if e['id'] not in observations],
                'llm_attempts':sum(e['kind']=='inference_started' for e in events),'llm_responses':sum(e['kind']=='model' for e in events),'shell_calls':sum(e['data']['kind']=='shell' for e in intents),'repair_attempts':sum(e['kind']=='repair_attempt' for e in events),'successful_repairs':sum(e['kind']=='repair_result' and e['data']['pass'] for e in events),'latest_result':next((e for e in reversed(events) if e['kind']=='observation'),None),'latest_action':intents[-1] if intents else None,'result_directory':str(store.workspace(task))}


def result(store,task):
    state=status(store,task);spec=store.specification(task)['spec'];events=store.events(task)
    state['current_verification']=WorkspaceContract(spec['conditions']).verify(store.workspace(task),events,{'report':state['report']}) if spec['conditions'] else {'pass':False,'reason':'No independent goal contract; UNVERIFIED'}
    state['artifacts']=metadata(snapshot(store.workspace(task)));state['verification_events']=[e for e in events if e['kind']=='external_verification'];return state


def main(argv=None):
    parser=argparse.ArgumentParser(description='NeuroOS local persistent tasks; 9B stays loaded')
    parser.add_argument('--state',default=str(DEFAULT));sub=parser.add_subparsers(dest='operation',required=True)
    p=sub.add_parser('submit');p.add_argument('goal');p.add_argument('--input');p.add_argument('--contract');p.add_argument('--allow-create',action='append',default=[]);p.add_argument('--allow-edit',action='append',default=[]);p.add_argument('--allow-remove',action='append',default=[]);p.add_argument('--seconds',type=int,default=1800);p.add_argument('--steps',type=int,default=12);p.add_argument('--shell-seconds',type=int,default=15);p.add_argument('--no-start',action='store_true');p.add_argument('--slice-actions',type=int)
    p=sub.add_parser('exec',help='Run an explicitly requested program in the same rootless workspace; no LLM')
    p.add_argument('--command',required=True);p.add_argument('--input');p.add_argument('--contract');p.add_argument('--seconds',type=int,default=120);p.add_argument('--pty',action='store_true');p.add_argument('--stdin-open',action='store_true');p.add_argument('--no-start',action='store_true')
    p=sub.add_parser('send');p.add_argument('task');p.add_argument('text',nargs='?');p.add_argument('--eof',action='store_true')
    for name in ['status','result','pause','cancel','events','attach','resume','worker','finalize','reconcile']:
        p=sub.add_parser(name);p.add_argument('task')
        if name in {'resume','worker'}:p.add_argument('--slice-actions',type=int)
        if name in {'events','attach'}:p.add_argument('--all',action='store_true')
        if name=='reconcile':p.add_argument('--discard-unconfirmed',action='store_true')
    p=sub.add_parser('chat');p.add_argument('--session');p.add_argument('--new',action='store_true');p.add_argument('--plain',action='store_true')
    p=sub.add_parser('details',help='Read task diagnostics separately from the conversation');p.add_argument('task',nargs='?');p.add_argument('--session')
    for name in ['chat-tasks','chat-events']:
        p=sub.add_parser(name);p.add_argument('--session',required=True);p.add_argument('--once',action='store_true')
    sub.add_parser('list');sub.add_parser('model')
    args=parser.parse_args(argv);st=open_store(args.state)
    try:
        if args.operation=='chat':
            from .chat import ChatStore,client
            import uuid
            session=ChatStore(st).session(uuid.uuid4().hex[:16] if args.new else args.session)
            from .chat_ui import open_ui
            if not args.plain and sys.stdin.isatty() and not os.environ.get('TMUX') and open_ui(st,session):return
            client(st,session);return
        elif args.operation=='details':
            from .chat import ChatStore
            from .presentation import details
            chat=ChatStore(st)
            tasks=chat.tasks(chat.session(args.session)) if not args.task else []
            task=args.task or (tasks[-1]['id'] if tasks else None)
            print(details(st,task) if task else 'В разговоре ещё нет заданий.');return
        elif args.operation in {'chat-tasks','chat-events'}:
            from .chat_ui import watch
            watch(st,args.session,args.operation,args.once);return
        elif args.operation=='submit':
            spec=json.loads(Path(args.contract).read_text()) if args.contract else {'policy':{},'conditions':None}
            tree=snapshot(Path(args.input).resolve()) if args.input else {}
            policy=spec['policy'];policy['create']=list(policy.get('create',[]))+args.allow_create;policy['remove']=list(policy.get('remove',[]))+args.allow_remove;policy['edit']={**policy.get('edit',{}),**{logical_path(p):tree[logical_path(p)]['sha256'] for p in args.allow_edit}}
            task=submit(st,args.goal,args.input,spec,{'seconds':args.seconds,'steps':args.steps,'shell_seconds':args.shell_seconds});out={'task':task,'status':'READY'} if args.no_start else launch(st,task,slice_actions=args.slice_actions)
        elif args.operation=='exec':
            if not 1<=args.seconds<=600:raise ValueError('process deadline must be 1..600 seconds')
            action={'kind':'shell','command':args.command}
            from .reliability import strict_action
            strict_action(action)
            spec=json.loads(Path(args.contract).read_text()) if args.contract else None
            task=submit(st,'User requested isolated process',args.input,spec,{'seconds':max(30,args.seconds),'steps':1,'shell_seconds':args.seconds,'process_action':action,'pty':args.pty,'open_stdin':args.stdin_open})
            out={'task':task,'status':'READY'} if args.no_start else launch(st,task)
        elif args.operation=='send':
            if not args.eof and args.text is None:raise ValueError('text or --eof required')
            out=send_input(st,args.task,args.text,args.eof)
        elif args.operation=='worker':out=worker(st,args.task,args.slice_actions)
        elif args.operation=='reconcile':out=worker(st,args.task,reconcile_only=True,discard_unconfirmed=args.discard_unconfirmed)
        elif args.operation=='finalize':finalize(st,args.task);out={'task':args.task,'finalized':True}
        elif args.operation=='resume':
            if unit_state(args.task).get('ActiveState') in {'active','activating'}:out={'task':args.task,'status':'controller_already_running','control':st.runtime(args.task)['control']}
            else:
                if st.runtime(args.task)['control']=='cancel':raise ValueError('cancellation already requested')
                st.runtime_update(args.task,control=None);out=launch(st,args.task,slice_actions=args.slice_actions)
        elif args.operation in {'pause','cancel'}:
            out=request(st,args.task,args.operation)
            if args.operation=='cancel' and unit_state(args.task).get('ActiveState') not in {'active','activating'}:out=launch(st,args.task)
        elif args.operation=='list':out=[status(st,r['id']) for r in st.db.execute('SELECT id FROM tasks ORDER BY created DESC')]
        elif args.operation=='status':out=status(st,args.task)
        elif args.operation=='result':out=result(st,args.task)
        elif args.operation=='model':
            from .model_adapter import ModelAdapter
            out=ModelAdapter().status()
        elif args.operation in {'events','attach'}:
            seen=0;offsets={}
            while True:
                for e in st.events(args.task):
                    if e['id']>seen and (args.all or e['kind'] not in {'model','inference_request'}):print(json.dumps(e,ensure_ascii=False),flush=True)
                    seen=max(seen,e['id'])
                if args.operation=='events':return
                for p in (st.root/(args.task+'.execution')).glob('operation-*.*'):
                    if p.suffix not in {'.stdout','.stderr'}:continue
                    with p.open('rb') as f:f.seek(offsets.get(str(p),0));data=f.read(262144);offsets[str(p)]=f.tell()
                    if data:print(p.name+': '+data.decode(errors='replace'),flush=True)
                if st.get(args.task)['status'] not in {'ready','running','checking','repairing'}:return
                time.sleep(.5)
        print(json.dumps(out,ensure_ascii=False,indent=2))
    except (ValueError,RuntimeError,subprocess.CalledProcessError) as exc:
        parser.exit(2,json.dumps({'error':type(exc).__name__,'message':str(exc)},ensure_ascii=False)+'\n')
    finally:st.db.close()

if __name__=='__main__':main()
