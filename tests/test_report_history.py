import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from weekly_trend.report_history import apply, empty, load, verify, validate
from weekly_trend.pipeline import write_json

class HistoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.issue='issue-'+'a'*20
  write_json(self.root/f'site/data/issues/{self.issue}.json',{'issue_id':self.issue,'is_demo':False,'title':'가상 증권사 인수 계약','company':'가상 증권사','topic':'가상 주제','evidence':[{'source_url':'https://example.com/test','source_name':'가상 출처','published_date':'2026-01-01'}]})
  self.p={'schema_version':'1.0','base_revision':0,'request_id':'b'*32,'public_acknowledged':True,'confirmed_reported':True,'report':{'id':'report-'+'c'*32,'title':'가상 인수 보고','key_text':'실제 전달했다고 가정한 시험 문구','reported_on':'2026-01-02','status':'reported','issue_ids':[self.issue],'reason':'시험 등록'}}
 def test_empty_not_invented(self):self.assertEqual(load(self.root),empty())
 def test_save_mirror_snapshot_and_idempotency(self):
  apply(self.root,self.p);self.assertEqual(verify(self.root)['revision'],1);self.assertTrue(apply(self.root,self.p)['duplicate_request'])
  self.assertEqual(load(self.root)['reports'][0]['versions'][0]['sources'][0]['title'],'가상 증권사 인수 계약')
 def test_explicit_confirmations(self):
  for flag in ['public_acknowledged','confirmed_reported']:
   p=copy.deepcopy(self.p);p[flag]=False
   with self.assertRaises(ValueError):apply(self.root,p)
  self.assertEqual(load(self.root),empty())
 def test_revision_conflict(self):
  apply(self.root,self.p);p=copy.deepcopy(self.p);p['request_id']='d'*32
  with self.assertRaises(ValueError):apply(self.root,p)
 def test_request_reuse_rejected(self):
  apply(self.root,self.p);p=copy.deepcopy(self.p);p['report']['title']='가상 변경'
  with self.assertRaises(ValueError):apply(self.root,p)
 def test_edit_cancel_keeps_previous_source_after_deleted(self):
  apply(self.root,self.p);(self.root/f'site/data/issues/{self.issue}.json').unlink()
  p=copy.deepcopy(self.p);p.update(base_revision=1,request_id='d'*32);p['report'].update(status='cancelled',reason='잘못된 완료 취소')
  apply(self.root,p);v=load(self.root)['reports'][0]['versions'];self.assertEqual(len(v),2);self.assertEqual(v[0]['status'],'reported');self.assertEqual(v[-1]['sources'],v[0]['sources'])
 def test_invalid_payloads(self):
  cases=[('title',''),('key_text',''),('reported_on','2026-02-30'),('reported_on','2999-01-01'),('status','draft'),('issue_ids',[]),('issue_ids',[self.issue,self.issue]),('issue_ids',['../../secret'])]
  for k,v in cases:
   p=copy.deepcopy(self.p);p['report'][k]=v
   with self.subTest(k=k,v=v),self.assertRaises(ValueError):apply(self.root,p)
 def test_unknown_cancel_and_demo_rejected(self):
  p=copy.deepcopy(self.p);p['report']['status']='cancelled'
  with self.assertRaises(ValueError):apply(self.root,p)
  path=self.root/f'site/data/issues/{self.issue}.json';d=json.loads(path.read_text());d['is_demo']=True;write_json(path,d)
  with self.assertRaises(ValueError):apply(self.root,self.p)
 def test_mirror_tamper(self):
  apply(self.root,self.p);write_json(self.root/'site/data/delivered-reports.json',empty())
  with self.assertRaises(ValueError):verify(self.root)
 def test_preserve_original_on_failed_write(self):
  apply(self.root,self.p);before=load(self.root);p=copy.deepcopy(self.p);p.update(base_revision=1,request_id='d'*32)
  real=write_json;calls=[]
  def fail_second(path,value):
   calls.append(path)
   if len(calls)==2:raise OSError('test')
   real(path,value)
  with patch('weekly_trend.report_history.write_json',side_effect=fail_second),self.assertRaises(OSError):apply(self.root,p)
  self.assertEqual(verify(self.root),before)
 def test_collection_rebuild_preserves_saved_report(self):
  from test_live_pipeline import LivePipelineTests, record
  harness=LivePipelineTests();harness.setUp();self.addCleanup(harness.doCleanups)
  harness.run_records([record()]);item=harness.latest()['candidates'][0]
  p=copy.deepcopy(self.p);p['report']['issue_ids']=[item['issue_id']]
  apply(harness.root,p);before=load(harness.root)
  harness.run_records([record()])
  self.assertEqual(verify(harness.root),before)
 def test_no_collected_data_mutation(self):
  before=(self.root/f'site/data/issues/{self.issue}.json').read_bytes();apply(self.root,self.p)
  self.assertEqual((self.root/f'site/data/issues/{self.issue}.json').read_bytes(),before)

if __name__=='__main__':unittest.main()
