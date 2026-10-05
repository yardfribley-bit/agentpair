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
  view.toggle();view.tick();position=view.position;view.toggle();self.assertEqual(view.position,position);self.assertFalse(view.timer.isActive());self.assertIn('先查',view.body.toPlainText())
  view.select('calls');self.assertIn('https://api.example.com/weather',view.body.toPlainText());self.assertIn('未记录关联返回',view.body.toPlainText());view.close()
