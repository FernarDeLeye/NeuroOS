"""References to actually displayed rows, using existing events as durable state."""
import copy,json,os,time
from pathlib import Path
from .fact_views import source_event

def process_identity(pid):
    """Public proc identity, never arguments/environment; no write or process control."""
    pid=int(pid)
    if pid<=0:raise ValueError('invalid process identity')
    root=Path('/proc')/str(pid)
    try:
        raw=(root/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
        boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return {'pid':pid,'start_ticks':int(fields[19]),'boot_id':boot,'name':raw[raw.find('(')+1:raw.rfind(')')],'state':fields[0],'rss_kib':int(fields[21])*os.sysconf('SC_PAGE_SIZE')//1024}
    except FileNotFoundError:return {'pid':pid,'unavailable':'gone'}
    except (PermissionError,ProcessLookupError,ValueError,IndexError,OSError) as exc:return {'pid':pid,'unavailable':'uncertain','error':type(exc).__name__}

def displayed(store,conversation,view_id):
    row=store.db.execute('SELECT e.* FROM events e JOIN task_workspaces w ON w.task=e.task WHERE e.id=? AND w.conversation=?',(view_id,conversation)).fetchone()
    if not row or row['kind']!='presentation_view':raise ValueError('reference must name a displayed view in this conversation')
    value=json.loads(row['data'])
    delivered=store.db.execute('SELECT 1 FROM conversation_view_deliveries WHERE view_event=? AND conversation=?',(view_id,conversation)).fetchone()
    if not delivered:
        # Legacy deliveries predate the explicit mapping. Only an exact already
        # saved assistant representation can establish that the list was shown.
        from .fact_views import render
        text=render(value)
        message=next((m for m in store.db.execute('SELECT id,text FROM conversation_messages WHERE conversation=? AND task=? AND role=? ORDER BY id',(conversation,row['task'],'assistant')) if text in m['text']),None)
        if not message:raise ValueError('this observation/view was not delivered as a list; ask what object the user means')
        with store.db:store.db.execute('INSERT OR IGNORE INTO conversation_view_deliveries VALUES(?,?,?)',(view_id,conversation,message['id']))
    source=source_event(store,conversation,value['source_event'])
    if value['rows']!=[source['rows'][i] for i in value['row_indices']]:raise ValueError('display/source mismatch')
    return value

def inspect(store,conversation,action,workspace=None):
    value=displayed(store,conversation,action['view_event']);position=action['position']
    if not value['offset']<position<=value['end']:raise ValueError('position was not in this displayed range; ask for the intended list/position')
    index=position-value['offset']-1;row=copy.deepcopy(value['rows'][index]);source=value['source']
    result={'view_event':action['view_event'],'position':position,'source_event':value['source_event'],'source':source,
            'row_index':value['row_indices'][index],'original':row,'original_observed_at':value['observed_at'],'checked_at':time.time()}
    if source.startswith('processes_'):
        old=row.get('identity');now=process_identity(row['pid']);result['current']=now
        if not old or old.get('unavailable') or any(k not in old for k in ['pid','boot_id','start_ticks']):result['status']='UNCERTAIN'
        elif now.get('unavailable')=='gone':result['status']='GONE'
        elif now.get('unavailable'):result['status']='UNCERTAIN'
        elif any(old.get(k)!=now.get(k) for k in ['pid','boot_id','start_ticks']):result['status']='REPLACED'
        else:result['status']='SAME_OBJECT'
    elif source=='files':
        from .workspace_tree import metadata,snapshot
        expected=source_event(store,conversation,value['source_event']).get('root_identity')
        try:actual=os.stat(workspace,follow_symlinks=False) if workspace else None
        except OSError:actual=None
        if expected is None or actual is None or [actual.st_dev,actual.st_ino]!=expected:
            result['current']=None;result['status']='UNCERTAIN';result['reason']='workspace_identity_changed_or_unknown'
        else:
            from .fact_views import observe_files
            current=next((r for r in observe_files(workspace)['rows'] if r['path']==row['path']),None)
            result['current']=current
            # A file content/path reference is not an inode-lifetime guarantee.
            result['status']='GONE' if current is None else 'UNCHANGED_CONTENT' if all(current.get(k)==row.get(k) for k in ['kind','sha256','size','target','mode']+ [k for k in ['device','inode','mtime_ns','ctime_ns'] if k in row]) else 'CHANGED'
    elif source in {'wifi','interfaces'}:
        from .fact_views import observe
        fresh=observe(source);key='bssid' if source=='wifi' else 'ifindex'
        matches=[r for r in fresh['rows'] if row.get(key) is not None and r.get(key)==row[key]]
        result['current_observation']={k:v for k,v in fresh.items() if k!='rows'}
        result['current']=matches
        result['status']='UNCERTAIN' if not fresh['available'] or row.get(key) is None or len(matches)>1 else 'NOT_OBSERVED' if not matches else 'OBSERVED_AGAIN'
        result['identity_scope']='access-point BSSID, not SSID' if source=='wifi' else 'interface index in host network namespace; not an eternal hardware identity'
    else:
        result['status']='HISTORICAL_ONLY';result['current']=None
    result['read_wall_s']=time.time()-result['checked_at']
    return result

def remember(store,conversation,action):
    value=displayed(store,conversation,action['view_event']);position=action['position']
    if not value['offset']<position<=value['end']:raise ValueError('position was not displayed')
    index=position-value['offset']-1
    return {'view_event':action['view_event'],'position':position,'source_event':value['source_event'],'source':value['source'],
            'row_index':value['row_indices'][index],'original':copy.deepcopy(value['rows'][index]),'original_observed_at':value['observed_at'],
            'checked_at':time.time(),'status':'HISTORICAL_ONLY','current':None,'read_wall_s':0,'freshness':'historical','current_check_performed':False}

def render_reference(value):
    import datetime
    safe=lambda s:''.join(c if c>=' ' and c!='\x7f' else repr(c)[1:-1] for c in str(s))
    stamp=lambda t:datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat(timespec='seconds')
    row=value['original'];name=row.get('name',row.get('ssid',row.get('path','объект')))
    status={'SAME_OBJECT':'Подтверждён тот же процесс (проверены загрузка ОС и время запуска).',
            'REPLACED':'Исходный процесс больше не подтверждён: идентификатор занят другим процессом.',
            'GONE':'Исходный объект больше не найден.', 'UNCERTAIN':'Текущее состояние исходного объекта не удалось подтвердить.',
            'UNCHANGED_CONTENT':'Содержимое и тип по исходному пути не изменились; непрерывность inode не подтверждается.',
            'CHANGED':'Объект по исходному пути изменился.', 'OBSERVED_AGAIN':'Объект снова наблюдается по сохранённому идентификатору.',
            'NOT_OBSERVED':'Объект не найден в новом наблюдении; это не доказывает его физического отсутствия.',
            'HISTORICAL_ONLY':'Доступно исходное наблюдение; новая проверка не выполнена.'}[value['status']]
    text='Объект №'+str(value['position'])+' ранее показанного списка: '+safe(name)+'.\nИсходное наблюдение: '+stamp(value['original_observed_at'])+'.\n'+status
    from .fact_views import public_cells,CATALOG
    text+='\nИсходные данные: '+'; '.join(public_cells(row,value.get('columns',CATALOG[value['source']][2])))+'.'
    if value['status']=='OBSERVED_AGAIN':text+='\nПоследнее наблюдение: '+'; '.join(public_cells(value['current'][0],value.get('columns',CATALOG[value['source']][2])))+'.'
    text+=('\nПрочитано из истории: ' if value['status']=='HISTORICAL_ONLY' else '\nПроверка: ')+stamp(value['checked_at'])+'.'
    if value['source'].startswith('processes_') and value['status']=='SAME_OBJECT':
        current=value['current'];text+='\nТекущее имя: '+safe(current.get('name','неизвестно'))+'; состояние Linux: '+str(current.get('state','неизвестно'))+'.'
    if value['source']=='wifi':text+=' Использован кэш NetworkManager, новое сканирование не выполнялось.'
    if value.get('interpretation'):text+='\nИнтерпретация модели: '+value['interpretation']
    return text
