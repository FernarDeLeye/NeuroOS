"""Source-bound read-only observations and exact data views, independent of language."""
import datetime
import os
import json
import re
import tempfile
import time
from pathlib import Path
from .system_snapshot import capture,escaped_fields

CATALOG={
 'files':('workspace','Файлы разрешённого workspace',['path','kind','size']),
 'os':('os.txt','Операционная система',['key','value']),
 'memory':('memory.txt','Память',['key','value']),
 'cpu':('cpu.txt','CPU',['key','value']),
 'disks':('filesystems.txt','Файловые системы',['filesystem','blocks_kib','used_kib','available_kib','capacity','mount']),
 'processes_memory':('processes-by-memory.tsv','Процессы по RSS',['name','rss_kib','cpu_percent','memory_percent']),
 'processes_cpu':('processes.tsv','Процессы по CPU',['name','cpu_percent','rss_kib','memory_percent']),
 'interfaces':('interfaces.json','Сетевые интерфейсы',['name','type','state','addresses','management']),
 'wifi':('wifi.txt','Wi-Fi: наблюдаемые точки доступа',['ssid','signal','security']),
 'services':('user-services.txt','Пользовательские службы',['name','load','active','substate','description']),
}

def parse(source,raw,extra):
    rows=[]
    if source in {'os','cpu','memory'}:
        separator='=' if source=='os' else ':'
        for line in raw.splitlines():
            if separator in line:
                key,value=line.split(separator,1)
                rows.append({'key':key.strip(),'value':value.strip().strip('"')})
    elif source.startswith('processes_'):
        for line in raw.splitlines()[1:]:
            m=re.match(r'^\s*(\d+)\s+(\d+)\s+(.*?)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s*$',line)
            if not m:raise ValueError('unrecognized process row')
            pid,ppid,name,cpu,memory,rss=m.groups()
            rows.append(dict(pid=int(pid),ppid=int(ppid),name=name,cpu_percent=float(cpu),memory_percent=float(memory),rss_kib=int(rss)))
    elif source=='wifi':
        for line in raw.splitlines():
            f=escaped_fields(line)
            if len(f) not in {4,5} or not f[1].isdigit():raise ValueError('unrecognized Wi-Fi record')
            if len(f)==5 and not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',f[4]):raise ValueError('invalid AP identity')
            rows.append(dict(ssid=f[0],signal=int(f[1]),security=f[2],in_use=f[3].strip()=='*')|({'bssid':f[4]} if len(f)==5 else {}))
    elif source=='interfaces':
        devices={}
        for line in extra.get('network-devices.txt','').splitlines():
            f=escaped_fields(line)
            if len(f)==3:devices[f[0]]=f
        for item in json.loads(raw):
            if not isinstance(item.get('addr_info'),list):raise ValueError('interface address observation is missing; cannot infer absence')
            nm=devices.get(item['ifname'])
            rows.append({'name':item['ifname'],'type':nm[1] if nm else 'link:'+item.get('link_type','unknown'),
                         'ifindex':item.get('ifindex'),'state':item.get('operstate','unknown'),'management':nm[2] if nm else 'unknown',
                         'addresses':[a['local']+'/'+str(a['prefixlen']) for a in item.get('addr_info',[]) if 'local' in a],
                         'type_source':'NetworkManager' if nm else 'ip.link_type'})
    elif source=='disks':
        for line in raw.splitlines()[1:]:
            f=line.split(None,5)
            if len(f)!=6:raise ValueError('unrecognized filesystem row')
            rows.append(dict(zip(CATALOG[source][2],f)))
    elif source=='services':
        for line in raw.splitlines():
            f=line.split(None,4)
            if len(f)<4:raise ValueError('unrecognized service row')
            rows.append(dict(zip(CATALOG[source][2],f+['']*(5-len(f)))))
    return rows

def observe(source,freshness='read_current'):
    if source not in CATALOG:raise ValueError('unknown public source')
    if freshness!='read_current':
        return {'source':source,'rows':[],'error':'Active scanning is not authorized by the read-only tool. Reading cache again is not a new scan.','available':False,'active_scan_performed':False}
    if source=='files':raise ValueError('files requires the authorised workspace adapter')
    started=time.time();file=CATALOG[source][0];names=[file]
    if source in {'interfaces','wifi'}:names+=['network-devices.txt','wifi-radio.txt']
    with tempfile.TemporaryDirectory(prefix='neuroos-observation-') as tmp:
        root=Path(tmp);receipts=capture(root,names)
        data={p.name:p.read_text() for p in root.iterdir() if p.name!='sources.json'}
    receipt=receipts[file];ok=receipt.get('exit_code')==0 and not receipt.get('output_truncated')
    error=receipt.get('error') or receipt.get('stderr') if not ok else None
    try:rows=parse(source,data.get(file,''),data) if ok else []
    except (ValueError,KeyError,TypeError) as exc:rows=[];ok=False;error=str(exc)
    if ok and source.startswith('processes_'):
        from .conversation_refs import process_identity
        for row in rows:row['identity']=process_identity(row['pid'])
    return {'source':source,'rows':rows,'available':ok,'error':error,'cached':source=='wifi',
            'observed_at':receipt['captured_at'],'read_wall_s':time.time()-started,'active_scan_performed':False,
            'receipts':receipts,'raw':data,'columns':CATALOG[source][2]}

def source_event(store,conversation,event_id):
    row=store.db.execute('SELECT e.* FROM events e JOIN task_workspaces w ON w.task=e.task WHERE e.id=? AND w.conversation=?',(event_id,conversation)).fetchone()
    if not row or row['kind']!='observation':raise ValueError('observation is not accessible in this conversation')
    data=json.loads(row['data']).get('host_dataset')
    if not isinstance(data,dict) or data.get('source') not in CATALOG:raise ValueError('not an original host observation')
    return data

def view(store,conversation,action):
    data=source_event(store,conversation,action['source_event'])
    if not data['available']:raise ValueError(data.get('error') or 'source unavailable')
    rows=list(enumerate(data['rows']));columns=action.get('columns') or data['columns']
    allowed=set(data['columns'])|{'pid','ppid'} if data['source'].startswith('processes_') else set(data['columns'])
    if not columns or any(c not in allowed for c in columns):raise ValueError('unknown columns')
    if data['source'].startswith('processes_'):
        columns=[c for c in columns if c not in {'pid','ppid'}]
        if not columns:raise ValueError('Process identifiers are available in diagnostics; select public result columns')
    key=action.get('sort_by')
    if key:
        if key not in allowed:raise ValueError('unknown sort field')
        rows.sort(key=lambda r:r[1][key],reverse=action.get('descending',False))
    offset=action.get('offset',0);limit=action.get('limit',0)
    if offset>len(rows):raise ValueError('offset exceeds observed result')
    selected=rows[offset:offset+limit] if limit else rows[offset:]
    return {'source_event':action['source_event'],'source':data['source'],'observed_at':data['observed_at'],
            'cached':data.get('cached',False),'columns':columns,'rows':[r for _,r in selected],
            'row_indices':[i for i,_ in selected],'total':len(rows),'offset':offset,'end':offset+len(selected),
            'sort_by':key,'descending':action.get('descending',False),'active_scan_performed':False,
            'interpretation':action.get('interpretation',''),'assumptions':action.get('assumptions',[])}

def public_cells(row,columns):
    result=[]
    for column in columns:
        item=row.get(column,'')
        if isinstance(item,list):item=', '.join(map(str,item)) or 'нет назначенных адресов'
        elif column=='ssid' and item=='':item='[скрытое имя]'
        text=str(item);text=''.join(c if c>=' ' else repr(c)[1:-1] for c in text)
        label={'name':'Имя','rss_kib':'RSS, КиБ','cpu_percent':'CPU, %','memory_percent':'RAM, %','ssid':'SSID','signal':'Сигнал, %','security':'Защита','addresses':'IP-адреса','management':'Управление NetworkManager','state':'Состояние канала','type':'Тип','key':'Параметр','value':'Значение'}.get(column,column)
        result.append(label+': '+text)
    return result

def render(value):
    stamp=datetime.datetime.fromtimestamp(value['observed_at'],datetime.timezone.utc).isoformat(timespec='seconds')
    header=CATALOG[value['source']][1]+'\n'
    header+=('Кэш NetworkManager прочитан ' if value['cached'] else 'Наблюдение Linux от ')+stamp+'.'
    if value['cached']:header+=' Новое сканирование не выполнялось.'
    header+=('\nПоказаны записи '+str(value['offset']+1)+'–'+str(value['end'])+' из '+str(value['total'])+'.') if value['rows'] else '\nВ выбранном диапазоне записей нет; всего '+str(value['total'])+'.'
    if value['sort_by']:header+=' Порядок: '+value['sort_by']+(' ↓' if value['descending'] else ' ↑')+'.'
    lines=[]
    for number,row in enumerate(value['rows'],value['offset']+1):
        cells=public_cells(row,value['columns'])
        lines.append(str(number)+'. '+'; '.join(cells))
    text=header+'\n'+'\n'.join(lines)
    if value.get('interpretation'):text+='\nИнтерпретация модели: '+value['interpretation']
    if value.get('assumptions'):text+='\nНеподтверждённые предположения: '+'; '.join(value['assumptions'])
    return text


def observe_files(root):
    import time
    from .workspace_tree import snapshot,metadata
    started=time.time();before=os.stat(root,follow_symlinks=False)
    rows=[{'path':p,**v} for p,v in metadata(snapshot(root,observe_metadata=True)).items() if p!='.tmp' and not p.startswith('.tmp/')]
    after=os.stat(root,follow_symlinks=False)
    if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise ValueError('workspace changed during observation')
    return {'root_identity':[before.st_dev,before.st_ino],'source':'files','rows':rows,'available':True,'error':None,'cached':False,'observed_at':started,'active_scan_performed':False,'columns':CATALOG['files'][2],'scope':'authorised private workspace; paths/content references, not arbitrary host files','receipts':{},'raw':{}}
