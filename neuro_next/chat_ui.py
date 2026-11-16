"""tmux layout is a user-unit UI; actual tasks remain separate existing units."""
import json,os,shlex,shutil,subprocess,sys,time
from pathlib import Path
from .chat import ChatStore
ROOT=Path(__file__).resolve().parents[1]

def argv(store,session,operation):return [sys.executable,'-m','neuro_next.persistent','--state',str(store.root),operation,'--session',session]
def build(root,session):
    from .persistent import open_store
    st=open_store(root);ChatStore(st).session(session);socket='neuroos-'+session
    def tmux(*args):return subprocess.run(['tmux','-f','/dev/null','-L',socket,*args],capture_output=True,text=True,check=True,timeout=10)
    main=shlex.join(argv(st,session,'chat')+['--plain'])
    tmux('new-session','-d','-s','neuroos','-x','140','-y','42',main)
    tmux('set-option','-t','neuroos','remain-on-exit','on')
    tmux('split-window','-h','-p','35','-t','neuroos:0.0',shlex.join(argv(st,session,'chat-tasks')))
    tmux('split-window','-v','-p','55','-t','neuroos:0.1',shlex.join(argv(st,session,'chat-events')))
    tmux('select-pane','-t','neuroos:0.0');st.db.close()
def ensure_ui(store,session):
    if not shutil.which('tmux'):return False
    from .runtime_support import UserSystemd
    bus=UserSystemd.connect();env=bus.environment();socket='neuroos-'+session;unit='neuroos-chat-ui-'+session+'.service'
    has=subprocess.run(['tmux','-L',socket,'has-session','-t','neuroos'],capture_output=True,timeout=5)
    if has.returncode:
        subprocess.run(['systemd-run',*__import__('neuro_next.runtime_support',fromlist=['unit_environment_args']).unit_environment_args(),'--user','--quiet','--collect','--unit='+unit,'--working-directory='+str(ROOT),'-p','Type=oneshot','-p','RemainAfterExit=yes','-p','MemoryMax=192M','-p','MemorySwapMax=0','-p','TasksMax=32','-p','NoNewPrivileges=yes',sys.executable,'-m','neuro_next.chat_ui',str(store.root),session],env=env,capture_output=True,text=True,timeout=20,check=True)
    dead=subprocess.run(['tmux','-L',socket,'display-message','-p','-t','neuroos:0.0','#{pane_dead}'],capture_output=True,text=True,timeout=5)
    if dead.stdout.strip()=='1':subprocess.run(['tmux','-L',socket,'respawn-pane','-k','-t','neuroos:0.0',shlex.join(argv(store,session,'chat')+['--plain'])],check=True,timeout=5)
    return {'socket':socket,'unit':unit}

def open_ui(store,session):
    ui=ensure_ui(store,session)
    if not ui:return False
    subprocess.run(['tmux','-L',ui['socket'],'attach-session','-t','neuroos'],check=True)
    return True

def watch(store,session,operation,once=False):
    chat=ChatStore(store);seen=set()
    while True:
        if operation=='chat-tasks':
            if not once:print('\033[2J\033[H',end='')
            print('NeuroOS — задачи')
            for t in chat.tasks(session):print(t['id'],t['status'],t['goal'][:80])
        else:
            for t in chat.tasks(session):
                for e in store.events(t['id']):
                    key=(t['id'],e['id'])
                    if key in seen:continue
                    seen.add(key)
                    if e['kind'] in {'intent','observation','controller_feedback','verification_failed','execution_stop'}:print(json.dumps({'task':t['id'],**e},ensure_ascii=False),flush=True)
        if once:return
        time.sleep(1)
if __name__=='__main__':build(sys.argv[1],sys.argv[2])

class TerminalInput:
    """Poll canonical terminal or pipe without a blocking reader thread."""
    def __init__(self):self.buffer=b'';self.eof=False
    def poll(self,timeout=.1):
        import select
        if b'\n' not in self.buffer and not self.eof:
            if not select.select([sys.stdin],[],[],timeout)[0]:return None
            data=os.read(sys.stdin.fileno(),4096)
            if not data:self.eof=True
            else:self.buffer+=data
        if b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1);return line.decode(errors='replace')+'\n'
        if self.eof:
            if self.buffer:line=self.buffer;self.buffer=b'';return line.decode(errors='replace')
            return ''
        return None


def plain_client(store,session=None):
    from .persistent import status,request,launch,unit_state
    from .presentation import message as render_message,details
    chat=ChatStore(store);id=chat.session(session)
    print('NeuroOS\n/status — ход работы, /pause — пауза, /cancel — отмена, /resume — продолжить.\n/details — диагностика, /history — разговор, /quit — отсоединиться.',flush=True)
    for m in chat.messages(id)[-8:]:print(('Вы: ' if m['role']=='user' else 'NeuroOS: ')+(m['text'] if m['role']=='user' else render_message(m['text'])))
    shown=max([m['id'] for m in chat.messages(id)] or [0]);reader=TerminalInput();next_poll=0
    def deliver():
        nonlocal shown
        for m in chat.messages(id):
            if m['id']>shown and m['role']=='assistant':print('\nNeuroOS: '+render_message(m['text'])+'\nВы> ',end='',flush=True)
            shown=max(shown,m['id'])
    print('Вы> ',end='',flush=True)
    try:
        while True:
            if time.monotonic()>=next_poll:
                next_poll=time.monotonic()+.4
                for task in chat.tasks(id):
                    if task['status'] not in {'running','ready','checking','repairing'}:chat.finish(task['id'])
                try:chat.dispatch(id)
                except ValueError as exc:print('\nNeuroOS: '+str(exc),flush=True)
            deliver()
            line=reader.poll()
            deliver()
            if line is None:continue
            if line=='':return
            text=line.strip()
            if not text:continue
            if text in {'/quit','/exit'}:return
            tasks=chat.tasks(id);latest=tasks[-1]['id'] if tasks else None
            if text=='/status':
                pending=chat.db.execute('SELECT count(*) FROM conversation_inbox WHERE conversation=? AND task IS NULL AND error IS NULL',(id,)).fetchone()[0]
                current=status(store,latest) if latest else None
                labels={'UNCERTAIN':'Исход действия пока не подтверждён; требуется сверка состояния.','INFERENCE':'Модель обрабатывает запрос.','EXECUTION':'Выполняется действие.','PUBLICATION':'Сохраняется результат.','UNVERIFIED':'Ответ получен; полной независимой проверки нет.','PAUSED':'Задание приостановлено.','CANCELLED':'Задание отменено.','BLOCKED':'Выполнение остановлено; причина сохранена.'}
                print('NeuroOS: '+(labels.get(current['stage'],'Состояние сохранено.') if current else 'Активного задания нет.')+' В очереди: '+str(pending)+'.',flush=True)
            elif text in {'/pause','/cancel','/resume'}:
                if text=='/cancel':
                    with store.db:store.db.execute("UPDATE conversation_inbox SET error='Cancelled by user' WHERE conversation=? AND task IS NULL AND error IS NULL",(id,))
                with store.db:store.db.execute('UPDATE conversations SET queue_paused=? WHERE id=?',(int(text=='/pause'),id))
                if not latest:print('NeuroOS: Очередь обновлена; активного задания нет.',flush=True)
                elif store.get(latest)['status'] in {'verified','unverified','cancelled'}:print('NeuroOS: Предыдущее задание уже завершено.',flush=True)
                elif text=='/resume':
                    if unit_state(latest).get('ActiveState') in {'active','activating','deactivating'}:print('NeuroOS: Дождитесь завершения текущей остановки.',flush=True)
                    else:store.runtime_update(latest,control=None);launch(store,latest);print('NeuroOS: Продолжение запрошено.',flush=True)
                else:
                    request(store,latest,text[1:])
                    if text=='/cancel':
                        if unit_state(latest).get('ActiveState') not in {'active','activating','deactivating'}:launch(store,latest)
                    print('NeuroOS: Остановка запрошена. Состояние сохраняется.',flush=True)
            elif text=='/history':
                for m in chat.messages(id):print(('Вы: ' if m['role']=='user' else 'NeuroOS: ')+(m['text'] if m['role']=='user' else render_message(m['text'])))
            elif text=='/details' or text.startswith('/details '):
                selected=text.split(maxsplit=1)[1] if ' ' in text else latest
                if selected and any(t['id']==selected for t in tasks):print(details(store,selected))
                else:print('NeuroOS: Задание не найдено.')
            elif text=='/tasks':
                for t in tasks:print(t['id'],t['status'],t['goal'][:120])
            else:
                try:
                    chat.enqueue(id,text);print('NeuroOS: Запрос получен. Если модель занята, он будет выполнен последовательно.',flush=True)
                    chat.dispatch(id)
                except ValueError as exc:print('NeuroOS: '+str(exc),flush=True)
            print('Вы> ',end='',flush=True)
    except KeyboardInterrupt:print('\nКлиент отсоединён. Задачи и история сохранены.')
