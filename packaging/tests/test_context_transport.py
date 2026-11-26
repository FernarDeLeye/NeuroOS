import importlib.util,json,unittest
from pathlib import Path
from neuro_next import context_transport as m
class Hardening(unittest.TestCase):
 def test_bool_and_integer_constraints_are_not_aliased(self):
  messages=[{'role':'system','content':'stable'},{'role':'user','content':'header'},{'role':'user','content':json.dumps({'postconditions':[{'equals':{'ready':1}}]})},{'role':'assistant','content':'action'},{'role':'user','content':json.dumps({'memory':{},'postconditions':[{'equals':{'ready':True}}]})},{'role':'user','content':'goal'}]
  self.assertEqual(json.loads(m.compact(messages)[-2]['content'])['postconditions'],[{'equals':{'ready':True}}])
 def test_missing_shared_null_field_is_not_invented(self):
  memory={'lists':[{'objects':[{'ref':'1:1','message':None},{'ref':'1:2'}]}]};self.assertEqual(m.compact_memory(memory),memory)
 def test_uncertain_changed_and_diagnostic_reference_are_not_projected(self):
  for status,extra,exitcode in [('UNCERTAIN',{},0),('CHANGED',{},0),('REPLACED',{},0),('HISTORICAL_ONLY',{},0),('SAME_OBJECT',{'error':'permission denied'},0),('SAME_OBJECT',{},False)]:
   packet={'memory':{},'results':[{'event':8,'kind':'reference_selection','task':'old','ref':'15:2','status':status},{'event':9,'kind':'observation','task':'old','data':{'exit_code':exitcode,'presentation_complete':True,'reference':{'view_event':15,'position':2,'selection_event':8,**extra}}}]};messages=[{'role':'system','content':'s'},{'role':'user','content':'h'},{'role':'user','content':json.dumps(packet)},{'role':'user','content':'g'}]
   self.assertEqual(json.loads(m.project(messages,'new',{'old'})[-2]['content']),packet)
if __name__=='__main__':unittest.main()
