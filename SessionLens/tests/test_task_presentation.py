import unittest
from sessionlens.task_presentation import arguments,readable_fields
class PresentationTests(unittest.TestCase):
 def test_command_urls_preserve_query_and_local_scope(self):
  args=arguments({'payload':{'command':'curl "https://api.example.com/weather?latitude=31&forecast_days=3" http://127.0.0.1:8000'}})
  fields=readable_fields(args)
  self.assertIn({'label':'纬度','key':'latitude','value':'31'},fields)
  self.assertTrue(any(f['label']=='本机地址' for f in fields))
  self.assertTrue(any(f['value']=='https://api.example.com/weather?latitude=31&forecast_days=3' for f in fields))
 def test_nested_tool_parameters_are_readable(self):
  fields=readable_fields({'toolName':'VideoGen','params':{'enable_audio':False,'prompt':'中文提示词'}})
  self.assertEqual(fields[0]['value'],'关闭');self.assertEqual(fields[1]['value'],'中文提示词')
 def test_native_reasoning_pause_and_tool_fields(self):
  import os
  os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
  from PySide6.QtWidgets import QApplication
  from sessionlens.task_view import TaskView
  app=QApplication.instance() or QApplication([])
  view=TaskView();view.load({'question':'查看思路','packet':{'fragments':[]},'understanding':{'overview':{'text':'工具报告完成','evidenceRefs':[]},'steps':[]},'presentation':{'source':'workbuddy','prompt':'测试任务','calls':[{'id':'c','name':'curl','fields':readable_fields({'command':'curl https://api.example.com/weather'}),'arguments':{},'returns':[]}],'frames':[{'reasoning':{'text':'先查天气，然后核对返回。'},'call':'c','nextReason':None}],'reasoning':[{'id':'r','text':'先查天气，然后核对返回。'}],'replies':[],'context':[],'included':1,'total':1}})
  view.toggle();view.tick();position=view.position;view.toggle();self.assertEqual(view.position,position);self.assertFalse(view.timer.isActive());self.assertIn('先查',view.plain_text())
  view.select('calls');self.assertTrue(view.tabs['calls'].isChecked());self.assertIn('https://api.example.com/weather',view.plain_text());self.assertIn('未记录关联返回',view.plain_text());view.select('context');self.assertTrue(view.brain.isHidden());self.assertTrue(view.tabs['context'].isChecked());view.close()
 def test_step_isolation_and_delivery_chips(self):
  import os
  os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
  from PySide6.QtWidgets import QApplication
  from sessionlens.task_view import TaskView
  app=QApplication.instance() or QApplication([])
  calls=[{'id':'search','name':'ToolSearch','arguments':{},'fields':[{'key':'queries','label':'搜索','value':'VideoGen'}],'returns':[{'text':'工具定义'}]}, {'id':'video','name':'DeferExecuteTool → VideoGen','arguments':{'params':{'prompt':'SSH','resolution':'1080P'}},'fields':[{'key':'resolution','label':'分辨率','value':'1080P'}],'returns':[{'text':'{"status":"completed","videos":[{"localPath":"/tmp/video.mp4"}]}'}]}, {'id':'deliver','name':'present_files','arguments':{},'fields':[],'returns':[{'text':'{"type":"present_files_result","files":["/tmp/video.mp4"]}'}]}]
  view=TaskView();view.load({'question':'做成了吗','packet':{'fragments':[]},'understanding':{'overview':{'text':'工具返回完成','evidenceRefs':[]},'steps':[]},'presentation':{'source':'workbuddy','prompt':'生成动画','updated':'2026-10-04','calls':calls,'frames':[],'reasoning':[],'replies':[],'context':[],'included':3,'total':3}})
  self.assertEqual([x['title'] for x in view.steps],['找工具','写提示词','生成视频','交付文件']);self.assertEqual(view.delivered.text(),'已交付');self.assertEqual(view.trust.text(),'未核验')
  view.choose_step(2);view.select('calls');app.processEvents();self.assertIn('1080P',view.plain_text());self.assertNotIn('工具定义',view.plain_text());self.assertEqual(len(view.step_buttons),4);view.close()
