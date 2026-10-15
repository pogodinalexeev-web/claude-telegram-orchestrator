import ast,json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Replies(unittest.TestCase):
 def setUp(self):
  tree=ast.parse((ROOT/'intake.py').read_text());self.tree=tree
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_reply_context')
  mt=ast.parse((ROOT/'media.py').read_text());extract=next(n for n in mt.body if isinstance(n,ast.FunctionDef) and n.name=='_extract_attachment_tuple')
  self.calls=[]
  def save(*a):self.calls.append(a);return 'saved','Resources/attachments/selected.jpg'
  self.g=dict(json=json,Path=Path,auto_save_attachment=save,log=lambda x:None)
  exec(compile(ast.Module(body=[extract,fn],type_ignores=[]),'reply-test','exec'),self.g)
 def runmsg(self,t,**kw):return self.g['_reply_context'](dict(chat={'id':7},reply_to_message=t,**kw))
 def test_text_quote(self):
  r=self.runmsg({'message_id':19,'from':{'username':'bot'},'text':'chosen'},quote={'text':'part'})
  d=json.loads(r.split('\n')[1]);self.assertEqual(d['text'],'chosen');self.assertEqual(d['quote'],'part');self.assertEqual(d['message_id'],19)
 def test_exact_album_photo(self):
  r=self.runmsg({'message_id':22,'media_group_id':'album','photo':[{'file_id':'small'},{'file_id':'chosen'}]})
  self.assertEqual(self.calls[0][2],'chosen');self.assertIn('selected.jpg',r);self.assertIn('Only this selected',r)
 def test_failure_preserves_caption(self):
  self.g['auto_save_attachment']=lambda *a:(_ for _ in ()).throw(OSError())
  r=self.runmsg({'caption':'keep','photo':[{'file_id':'x'}]});self.assertIn('keep',r);self.assertIn('download failed',r)
 def test_voice_saved_not_transcribed(self):
  r=self.runmsg({'voice':{'file_id':'voice'}});self.assertEqual(self.calls[0][1],'voice');self.assertIn('attachment_path',r)
 def test_external(self):self.assertIn('unavailable',self.runmsg({},external_reply={'message_id':9}))
 def test_no_reply(self):self.assertEqual(self.runmsg({}),'')
 def test_each_intake_path(self):
  # 08.10.2026: тело приёма уехало во внутренние функции под закреплённой темой
  for name in ['_handle_album_in_topic','_handle_burst_in_topic','_handle_message']:
   fn=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
   self.assertTrue(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_reply_context' for n in ast.walk(fn)),name)
if __name__=='__main__':unittest.main()
