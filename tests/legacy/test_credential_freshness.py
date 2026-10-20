"""Synthetic regression coverage for fresh evidence, review versions and asset merges."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentpair.credential_threats import CredentialThreats
from agentpair.devices import DeviceStore


class CredentialFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=DeviceStore(Path(self.tmp.name)/'devices.db')
        self.alias=self.enroll()
        self.engine=SimpleNamespace(get=lambda task_id:{'id':task_id,'status':'completed','messages':[
            {'stage':'review','answer':{'securityEvidenceValidated':True,'finalAnswer':'已复核当时的凭据片段。','findings':[]}}]})

    def tearDown(self):self.tmp.cleanup()

    def enroll(self,owner='alice'):
        return self.store.enroll(self.store.pairing(owner)['code'],'Synthetic Mac')

    def record(self,n=1,device=None,text='云主机登录：root/Kj9sF3p8Qv1!',captured=100,received=110,session='original'):
        r={'id':f'{n:064x}','source':'workbuddy_network_context','timestamp':captured,'sessionId':session,
           'body':json.dumps({'messages':[{'role':'system','content':text},{'role':'user','content':'<user_query>检查报错</user_query>'}]},ensure_ascii=False)}
        with patch('agentpair.devices.time.time',return_value=received):
            self.store.ingest_model_context((device or self.alias)['token'],{'requests':[r]})
        return r

    def finding(self,device=None):
        return self.store.credential_threats.inventory(device)['items'][0]

    def review(self,fid=None):
        fid=fid or self.finding()['id'];packet=self.store.credential_threats.review_packet('alice',fid)
        self.store.credential_threats.link_review(fid,packet,'synthetic-review')
        return packet

    def test_clean_new_input_updates_received_and_scan_coverage_without_fabricating_threat(self):
        self.record(text='普通任务背景',received=200)
        data=self.store.credential_threats.inventory()
        self.assertEqual(data['items'],[])
        self.assertEqual((data['coverage']['lastReceivedAt'],data['coverage']['lastScannedAt']),(200,200))
        self.assertEqual((data['coverage']['receivedRecords'],data['coverage']['scannedRecords']),(1,1))
        self.assertEqual(data['coverage']['collector'],'applens')

    def test_replay_checks_freshness_without_changing_evidence_or_verification_receipt(self):
        r=self.record();before=self.finding();self.review()
        with patch('agentpair.devices.time.time',return_value=300):
            self.store.ingest_model_context(self.alias['token'],{'requests':[r]})
        after=self.finding()
        self.assertEqual(before['evidenceRevision'],after['evidenceRevision'])
        self.assertFalse(self.store.credential_threats.review(after['id'],self.engine)['stale'])
        self.assertEqual(after['lastScannedAt'],300)
        self.assertEqual(after['lastReceivedAt'],300)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT received FROM credential_captures').fetchone()[0],110)
        self.assertEqual(self.store.credential_threats.inventory()['coverage']['lastReceivedAt'],300)

    def test_new_evidence_outside_display_sample_stales_old_review_and_retains_answer(self):
        for n in range(1,14):self.record(n,captured=100+n,received=200+n)
        before=self.finding();self.assertEqual(len(before['evidence']),12)
        self.review()
        # This deliberately sorts outside the displayed 12 records.
        self.record(14,captured=1,received=300)
        after=self.finding();self.assertEqual(before['evidence'],after['evidence'])
        self.assertNotEqual(before['evidenceRevision'],after['evidenceRevision'])
        review=self.store.credential_threats.review(after['id'],self.engine)
        self.assertTrue(review['stale']);self.assertEqual(review['staleReason'],'evidence_changed')
        self.assertEqual(review['answer'],'已复核当时的凭据片段。')

    def test_packet_version_is_not_advanced_when_evidence_arrives_before_link(self):
        self.record();fid=self.finding()['id'];packet=self.store.credential_threats.review_packet('alice',fid)
        self.record(2,captured=120,received=130)
        self.store.credential_threats.link_review(fid,packet,'synthetic-review')
        self.assertTrue(self.store.credential_threats.review(fid,self.engine)['stale'])

    def test_replaced_primary_body_keeps_old_review_as_stale(self):
        self.record();fid=self.finding()['id'];self.review()
        self.record(text='普通任务背景',received=200)
        review=self.store.credential_threats.review(fid,self.engine)
        self.assertTrue(review['stale']);self.assertTrue(review['answer'])
        self.assertEqual(self.store.credential_threats.inventory()['items'],[])

    def test_missing_legacy_engine_task_does_not_break_access_to_current_findings(self):
        r=self.record();f=self.finding();self.review()
        def missing(task_id):raise KeyError(task_id)
        unavailable=SimpleNamespace(get=missing)
        review=self.store.credential_threats.review(f['id'],unavailable)
        self.assertEqual(review['status'],'unavailable');self.assertTrue(review['stale'])
        self.assertEqual(len(self.store.credential_threats.inventory()['items']),1)
        self.store.link_security_review(self.alias['deviceId'],r['id'],f['evidence'][0]['bodySHA256'],'missing-task')
        self.assertEqual(self.store.security_review(self.alias['deviceId'],r['id'],f['evidence'][0]['bodySHA256'],unavailable)['status'],'unavailable')

    def test_legacy_schema_review_survives_and_is_explicitly_unversioned(self):
        r=self.record();fid=self.finding()['id']
        with self.store.connect() as db:
            db.execute('DROP TABLE credential_reviews')
            db.execute('CREATE TABLE credential_reviews(finding_id TEXT PRIMARY KEY,request_id TEXT,body_hash TEXT,task_id TEXT)')
            db.execute('INSERT INTO credential_reviews VALUES(?,?,?,?)',(fid,r['id'],hashlib.sha256(r['body'].encode()).hexdigest(),'legacy-review'))
        threats=CredentialThreats(self.store.connect)
        review=threats.review(fid,self.engine)
        self.assertEqual(review['taskId'],'legacy-review');self.assertTrue(review['answer'])
        self.assertTrue(review['stale']);self.assertEqual(review['staleReason'],'legacy_evidence_version_unknown')

    def test_merge_rebuilds_detection_and_preserves_review_workflow_remediation_and_old_links(self):
        r=self.record();old=self.finding();fid=old['id'];self.review()
        self.store.link_security_review(self.alias['deviceId'],r['id'],old['evidence'][0]['bodySHA256'],'input-review')
        threats=self.store.credential_threats
        threats.manage('alice',fid,'assign',{'assignee':'负责人','due':'2099-10-09'})
        with patch('agentpair.credential_threats.time.time',return_value=200):
            threats.manage('alice',fid,'submit',{'note':'已清理背景','actions':{'cleanedContext':True}})
        self.record(2,text='普通任务背景',captured=220,received=230,session='new')
        canonical=self.enroll()
        self.store.merge_registrations('alice',canonical['deviceId'],[self.alias['deviceId']])
        new=self.finding(self.alias['deviceId'])
        self.assertEqual(new['deviceId'],canonical['deviceId']);self.assertEqual(new['evidenceRevision'],old['evidenceRevision'])
        self.assertIn(fid,new['aliases']);self.assertEqual(new['workflow']['assignee'],'负责人')
        self.assertEqual(new['workflow']['state'],'pending_verification')
        self.assertEqual(new['verification']['state'],'not_observed')
        self.assertFalse(threats.review(fid,self.engine)['stale'])
        self.assertEqual(self.store.security_review(canonical['deviceId'],r['id'],old['evidence'][0]['bodySHA256'],self.engine)['taskId'],'input-review')
        threats.manage('alice',fid,'close',{'note':'新会话验证完成','credentialRevokedConfirmed':True})
        self.assertEqual(self.finding()['workflow']['state'],'closed')
        self.store.merge_registrations('alice',canonical['deviceId'],[self.alias['deviceId']])
        self.assertEqual(len(self.finding()['workflow']['events']),3)

    def test_merge_combines_existing_asset_evidence_and_marks_source_review_stale(self):
        self.record();fid=self.finding()['id'];self.review()
        canonical=self.enroll();self.record(2,canonical,captured=120,received=130)
        self.store.merge_registrations('alice',canonical['deviceId'],[self.alias['deviceId']])
        data=self.store.credential_threats.inventory()
        self.assertEqual(len(data['items']),1);self.assertEqual(data['items'][0]['networkRecords'],2)
        review=self.store.credential_threats.review(data['items'][0]['id'],self.engine)
        self.assertTrue(review['stale']);self.assertTrue(review['answer'])

    def test_startup_repairs_prior_build_merge_and_repeated_startup_does_not_duplicate_workflow(self):
        r=self.record();old=self.finding();self.review()
        self.store.credential_threats.manage('alice',old['id'],'assign',{'assignee':'负责人','due':'2099-10-09'})
        self.store.link_security_review(self.alias['deviceId'],r['id'],old['evidence'][0]['bodySHA256'],'old-input-review')
        canonical=self.enroll()
        # Older builds moved only raw context and hid the former device.
        with self.store.connect() as db:
            db.execute('INSERT INTO device_aliases VALUES(?,?,?)',(self.alias['deviceId'],canonical['deviceId'],'alice'))
            db.execute('UPDATE applens_model_context SET device_id=? WHERE device_id=?',(canonical['deviceId'],self.alias['deviceId']))
        self.assertEqual(self.store.credential_threats.inventory()['items'],[])
        restored=DeviceStore(Path(self.tmp.name)/'devices.db')
        finding=restored.credential_threats.inventory()['items'][0]
        self.assertEqual(finding['deviceId'],canonical['deviceId']);self.assertEqual(finding['workflow']['assignee'],'负责人')
        self.assertFalse(restored.credential_threats.review(old['id'],self.engine)['stale'])
        self.assertEqual(restored.security_review(canonical['deviceId'],r['id'],old['evidence'][0]['bodySHA256'],self.engine)['taskId'],'old-input-review')
        again=DeviceStore(Path(self.tmp.name)/'devices.db')
        self.assertEqual(again.credential_threats.inventory()['items'],restored.credential_threats.inventory()['items'])

    def test_merge_chain_preserves_device_tokens_and_original_finding_links(self):
        self.record();first=self.finding()['id'];self.review()
        middle=self.enroll();last=self.enroll()
        self.store.merge_registrations('alice',middle['deviceId'],[self.alias['deviceId']])
        second=self.finding()['id']
        self.store.merge_registrations('alice',last['deviceId'],[middle['deviceId']])
        finding=self.finding()
        self.assertEqual(self.store.identity(self.alias['token'])['id'],last['deviceId'])
        self.assertEqual(self.store.identity(middle['token'])['id'],last['deviceId'])
        self.assertEqual(set(finding['aliases']),{first,second})
        self.assertFalse(self.store.credential_threats.review(first,self.engine)['stale'])

    def test_conflicting_request_merge_is_atomic_and_owner_boundary_is_enforced(self):
        self.record();canonical=self.enroll();self.record(device=canonical,text='普通背景')
        with self.assertRaises(ValueError):self.store.merge_registrations('alice',canonical['deviceId'],[self.alias['deviceId']])
        self.assertEqual(len(self.store.list('alice')),2)
        self.assertEqual(self.store.identity(self.alias['token'])['id'],self.alias['deviceId'])
        stranger=self.enroll('bob')
        with self.assertRaises(PermissionError):self.store.merge_registrations('alice',canonical['deviceId'],[stranger['deviceId']])

    def test_model_analysis_can_select_evidence_outside_recent_fifty(self):
        for n in range(1,53):self.record(n,text='普通背景',captured=n,received=n+100)
        packet,_=self.store.model_analysis_context('alice',self.alias['deviceId'],f'{1:064x}')
        self.assertEqual(packet['request']['id'],f'{1:064x}')


if __name__=='__main__':unittest.main()
