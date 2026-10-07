import json
import unittest

from agentpair.collection_assistant import current_context_input


class ContextFormatsTests(unittest.TestCase):
    def test_generation_context_last_explicit_input(self):
        body='<user_query>旧 SSH 视频</user_query>\n背景资料\n<user_query>查上海天气</user_query>'
        self.assertEqual(current_context_input({'body':body}), '查上海天气')

    def test_history_and_system_reminder_markers_do_not_create_current_input(self):
        for wrapper in ('cb_summary','conversation_history_summary','system-reminder'):
            body='<'+wrapper+'><user_query>旧 SSH 视频</user_query></'+wrapper+'>'
            self.assertEqual(current_context_input({'body':body}), '')
            self.assertEqual(current_context_input({'body':'<user_query>查上海天气</user_query>'+body}), '查上海天气')

    def test_plain_background_or_malformed_marker_is_not_a_user_message(self):
        for body in ('系统背景中提到查上海天气','<user_query>旧记录缺少结束标签','',None,{},[]):
            self.assertEqual(current_context_input({'body':body}), '')

    def test_json_network_request_does_not_extract_tags_from_system_background(self):
        body=json.dumps({'messages':[{'role':'system','content':'<user_query>旧 SSH 视频</user_query>'},
            {'role':'user','content':'查上海天气'}]})
        self.assertEqual(current_context_input({'body':body}), '查上海天气')
        self.assertEqual(current_context_input({'body':json.dumps({'messages':None})}), '')

    def test_generation_input_head_tail_preserves_current_requirement(self):
        text='说明 '*1500+'当前目标查上海天气'
        result=current_context_input({'body':'<user_query>'+text+'</user_query>'})
        self.assertLessEqual(len(result),1800)
        self.assertIn('当前目标查上海天气',result)


if __name__=='__main__': unittest.main()
