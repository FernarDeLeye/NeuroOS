"""Conservative discourse guard for unanchored singular pronouns.

No tool/source routing. Explicit names/ordinals and delivered single-object focus
remain model-resolved. Unknown descriptive constructions may need clarification.
"""
import json,re
PERSONAL={'он','она','оно','его','ее','него','нее','ему','ей','нем','ней','ним','it','him','her','he','she'}
ORDINAL=re.compile(r'\b(?:перв|втор|трет|четверт|пят|шест|седьм|восьм|девят|десят|последн|предпоследн)\w*\b|\b(?:first|second|third|fourth|last)\b|(?:№|#)\s*\d+|\b\d+[- ](?:й|я|е|го|му)\b')
def normal(text):return text.casefold().replace('ё','е')

def resolution(runtime):
 text=normal(runtime.store.get(runtime.task)['goal'])
 if not PERSONAL.intersection(re.findall(r'\w+',text)) or ORDINAL.search(text):return None
 db=runtime.store.db
 utterance=db.execute("SELECT min(id) FROM conversation_messages WHERE conversation=? AND task=? AND role='user'",(runtime.id,runtime.task)).fetchone()[0]
 if utterance is None:return None
 last=db.execute('SELECT e.id,e.data,d.message FROM conversation_view_deliveries d JOIN events e ON e.id=d.view_event WHERE d.conversation=? AND d.message<? ORDER BY e.id DESC LIMIT 1',(runtime.id,utterance)).fetchone()
 if not last:return None
 latest=db.execute("SELECT id,task FROM conversation_messages WHERE conversation=? AND role='assistant' AND id<? ORDER BY id DESC LIMIT 1",(runtime.id,utterance)).fetchone()
 if latest and latest['id']!=last['message']:
  pending=db.execute("SELECT 1 FROM events WHERE task=? AND kind='reference_clarification'",(latest['task'],)).fetchone()
  model=db.execute("SELECT data FROM events WHERE task=? AND kind='model' ORDER BY id DESC LIMIT 1",(latest['task'],)).fetchone()
  action=json.loads(model['data']).get('action',{}) if model else {}
  if not pending and not (action and action.get('kind')=='reply' and action.get('basis')=='clarification'):return None
 view=json.loads(last['data'])
 if len(view['rows'])<=1:return None
 # Attribute tables (OS/memory key-value rows) are not multiple entities.
 if not any(any(k in row for k in ['pid','path','bssid','ifindex','name','mount','ssid']) for row in view['rows']):return None
 # Explicit textual identifiers can ground descriptions; no guessed matching.
 for row in view['rows']:
  for key in ['name','ssid','path','bssid']:
   label=normal(str(row.get(key) or ''))
   if len(label)>1 and re.search(r'(?<!\w)'+re.escape(label)+r'(?!\w)',text):return None
  if row.get('pid') is not None and re.search(r'\bpid\s*[:=]?\s*'+str(row['pid'])+r'\b',text):return None
 # A guess inside THIS task cannot establish its own antecedent. Only an actually
 # delivered reference from a previous task, after this list, is a discourse anchor.
 rows=db.execute("SELECT e.* FROM events e JOIN task_workspaces w ON w.task=e.task WHERE w.conversation=? AND e.task!=? AND e.kind='reference_view' AND e.id>? ORDER BY e.id DESC",(runtime.id,runtime.task,last['id']))
 from .conversation_refs import render_reference
 for row in rows:
  value=json.loads(row['data']);rendered=render_reference(value)
  if any(rendered in m['text'] for m in db.execute("SELECT text FROM conversation_messages WHERE conversation=? AND task=? AND role='assistant' AND id<?",(runtime.id,row['task'],utterance))):return None
 return {'needs_clarification':True,'reason':'singular_pronoun_without_delivered_unique_anchor','view':last['id'],'message':last['message'],'source':view['source'],'positions':[view['offset']+1,view['end']],'candidate_count':len(view['rows']),'nothing_selected':True}
