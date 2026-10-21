"""Receipt-only credential queries must avoid the large original JSON table."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentpair.credential_threats import CredentialThreats
from agentpair.devices import DeviceStore


class CredentialReceiptIndexTests(unittest.TestCase):
    def test_existing_source_gets_one_covering_index_and_targeted_statistics(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'devices.db';store=DeviceStore(path)
            owners=[store.enroll(store.pairing(owner)['code'],'Synthetic') for owner in ('alice','bob')]
            with store.connect() as db:
                # Recreate a pre-migration source while retaining its existing
                # primary key, receipt and JSON timestamp indexes.
                db.execute('DROP INDEX applens_model_context_device_request_received')
                for owner in owners:
                    for index in range(200):
                        device=owner['deviceId'];request=f'fixture-{index:04d}'
                        body=json.dumps({'timestamp':index,'body':'synthetic background '*600})
                        db.execute('INSERT INTO applens_model_context VALUES(?,?,?,?)',(device,request,body,1000+index))
                        db.execute('INSERT INTO credential_captures(device_id,request_id,body_hash,captured,received,session,source,scanned) VALUES(?,?,?,?,?,?,?,?)',
                                   (device,request,'fixture-hash',index,1000+index,'fixture-session','workbuddy_network_context',1000+index))
                        if index%2==0:
                            db.execute('INSERT INTO credential_occurrences VALUES(?,?,?,?,?)',('fixture-finding-'+device,device,request,'fixture-hash','{}'))
                trace=[];db.set_trace_callback(trace.append)
                threats=CredentialThreats.__new__(CredentialThreats);threats.connect=lambda:nullcontext(db)
                global_before=threats.inventory()['coverage']
                count_sql=next(sql for sql in trace if sql.startswith('SELECT max(c.received) AS latest,count(*) AS records FROM applens_model_context'))
                device_before=threats.inventory(owners[0]['deviceId'])['coverage']
                device_sql=next(sql for sql in reversed(trace) if sql.startswith('SELECT max(c.received) AS latest,count(*) AS records FROM applens_model_context'))
                evidence_before=[tuple(row) for row in threats._occurrences(db,'fixture-finding-'+owners[0]['deviceId'])]
                occurrence_sql=trace[-1]
                db.set_trace_callback(None)
                original_rows=[tuple(row) for row in db.execute('SELECT * FROM applens_model_context ORDER BY device_id,request_id')]
            # Isolate the schema migration from unrelated startup detector work.
            statements=[];connect=DeviceStore.connect
            def traced_connect(instance):
                manager=connect(instance)
                from contextlib import contextmanager
                @contextmanager
                def traced():
                    with manager as db:
                        db.set_trace_callback(statements.append)
                        yield db
                return traced()
            with patch.object(DeviceStore,'connect',traced_connect),patch.object(CredentialThreats,'backfill'):
                migrated=DeviceStore(path)
            analyze=[sql for sql in statements if sql.upper().startswith('ANALYZE')]
            self.assertEqual(analyze,['ANALYZE applens_model_context_device_request_received'])
            with migrated.connect() as db:
                index='applens_model_context_device_request_received'
                self.assertEqual([row['name'] for row in db.execute('PRAGMA index_info('+index+')')],['device_id','request_id','received'])
                for query in (count_sql,device_sql,occurrence_sql):
                    plan=[row['detail'] for row in db.execute('EXPLAIN QUERY PLAN '+query)]
                    with self.subTest(query=query.split(' FROM ')[0]):
                        self.assertTrue(any('COVERING INDEX '+index in detail for detail in plan),plan)
                        if query==occurrence_sql:
                            self.assertTrue(any('SEARCH m USING COVERING INDEX '+index in detail for detail in plan),plan)
                self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM applens_model_context ORDER BY device_id,request_id')],original_rows)
                self.assertEqual(tuple(db.execute(count_sql).fetchone()),(global_before['lastReceivedAt'],global_before['receivedRecords']))
                self.assertEqual(tuple(db.execute(device_sql).fetchone()),(device_before['lastReceivedAt'],device_before['receivedRecords']))
                self.assertEqual([tuple(row) for row in db.execute(occurrence_sql)],evidence_before)
            statements.clear()
            with patch.object(DeviceStore,'connect',traced_connect),patch.object(CredentialThreats,'backfill'):
                DeviceStore(path)
            self.assertFalse(any(sql.upper().startswith('ANALYZE') for sql in statements),'Existing populated index statistics must be reused')


if __name__=='__main__':unittest.main()
