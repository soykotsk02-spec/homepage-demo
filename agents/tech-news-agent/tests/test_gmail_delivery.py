import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gmail_delivery import verified_send, verified_existing, GmailMailer


class EvidenceTests(unittest.TestCase):
    def event(self,result=None,status='completed',error=None):
        return json.dumps({'type':'item.completed','item':{'type':'mcp_tool_call','server':'codex_apps',
                          'tool':'gmail.send_email','status':status,'result':result,'error':error}})

    def test_real_structured_tool_id(self):
        ids,attempted,events=verified_send(self.event({'structured_content':{'id':'0000000000000001','label_ids':['SENT']}}))
        self.assertEqual(ids,['0000000000000001'])
        self.assertTrue(attempted)

    def test_nested_json_text_tool_result(self):
        result={'content':[{'type':'text','text':json.dumps({'id':'0000000000000001'})}]}
        self.assertEqual(verified_send(self.event(result))[0],['0000000000000001'])

    def test_model_claim_is_not_delivery_proof(self):
        text=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'sent 0000000000000001'}})
        self.assertEqual(verified_send(text)[:2],([],False))

    def test_actual_send_arguments_and_sender_are_bound_to_receipt(self):
        expected={'sender':'sender@example.com','recipient':'me@example.com','subject':'Exact','html':'<p>report</p>'}
        profile={'type':'item.completed','item':{'id':'profile','type':'mcp_tool_call','tool':'gmail.get_profile',
                 'status':'completed','result':{'structured_content':{'email':'sender@example.com'}}}}
        send=json.loads(self.event({'structured_content':{'id':'0000000000000001'}}))
        send['item']['id']='send1'
        args={'to':'me@example.com','subject':'Exact','payload':{'mime_type':'text/html','body':{'content':'<p>report</p>'}}}
        send['item']['arguments']=args
        self.assertEqual(verified_send(json.dumps(profile)+'\n'+json.dumps(send),expected)[0],['0000000000000001'])
        args['to']='wrong@example.com'
        self.assertEqual(verified_send(json.dumps(profile)+'\n'+json.dumps(send),expected)[0],[])

    def test_second_unconfirmed_send_prevents_success_claim(self):
        first=json.loads(self.event({'structured_content':{'id':'0000000000000001'}}))
        first['item']['id']='send1'
        second=json.loads(self.event(error={'message':'timeout'}))
        second['item']['id']='send2'
        self.assertEqual(verified_send(json.dumps(first)+'\n'+json.dumps(second))[0],[])

    def test_failed_or_unfinished_send_is_uncertain(self):
        for line in [self.event({'isError':True,'id':'0000000000000001'}),self.event(status='in_progress'),self.event(error={'message':'timeout'})]:
            self.assertEqual(verified_send(line)[:2],([],True))

    def test_injected_or_multiple_recipient_rejected(self):
        for addr in ['a@example.com,b@example.com','a@example.com\r\nBcc:b@example.com']:
            with self.assertRaises(ValueError):
                GmailMailer({'mail':{'recipient':addr,'username':'a@example.com'}})

    def test_existing_message_requires_exact_headers_and_sent_label(self):
        message={'id':'0000000000000001','label_ids':['SENT'],'payload':{'headers':[
            {'name':'Subject','value':'Exact subject'},{'name':'To','value':'me@example.com'},
            {'name':'From','value':'Sender <sender@example.com>'}]}}
        records=[{'tool':'gmail.read_email','status':'completed','result':{'structured_content':message}}]
        self.assertEqual(verified_existing(records,'me@example.com','sender@example.com','Exact subject'),['0000000000000001'])
        self.assertEqual(verified_existing(records,'other@example.com','sender@example.com','Exact subject'),[])
        self.assertEqual(verified_existing(records,'me@example.com','sender@example.com','Different subject'),[])


if __name__=='__main__':unittest.main()
