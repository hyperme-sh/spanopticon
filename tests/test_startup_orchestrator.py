import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest

from startup.orchestrator import run,STEPS


def validate(config):
    if config!={'binding':'configured'}:raise ValueError('Complete configuration required')
    return config


def fingerprint(config):return hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'state';self.config={'binding':'configured'};self.calls=[]
    def call(self,step,config,prior):
        self.calls.append(step)
        self.assertEqual(list(prior),list(STEPS[:STEPS.index(step)]))
        return {'mapping_sha256':fingerprint(config),'person_id':'one-person','status':'bound' if step=='auth' else 'reconciled'}
    def execute(self,call=None,config=None):
        return run(config or self.config,self.directory,call or self.call,validate=validate,fingerprint=fingerprint)
    def test_sequence_and_repeated_invocation(self):
        first=self.execute();self.assertEqual(self.execute(),first)
        self.assertEqual(self.calls,list(STEPS)*2)
        self.assertEqual((self.directory/'state.json').stat().st_mode&0o777,0o600)
    def test_concurrent_invocations_serialize_all_steps(self):
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:self.execute(),range(8)))
        self.assertTrue(all(result==results[0] for result in results))
        self.assertEqual(self.calls,list(STEPS)*8)
    def test_failure_at_every_step_resumes_after_reverification(self):
        for failed in STEPS:
            with self.subTest(step=failed),tempfile.TemporaryDirectory() as directory:
                self.directory=Path(directory)/'state';self.calls=[]
                def interrupt(step,config,prior):
                    if step==failed:raise RuntimeError('interrupted')
                    return self.call(step,config,prior)
                with self.assertRaises(RuntimeError):self.execute(interrupt)
                self.assertEqual(self.execute()['status'],'reconciled')
                self.assertEqual(self.calls[-4:],list(STEPS))
    def test_changed_earlier_owner_refuses_later_steps(self):
        self.execute();self.calls=[]
        def changed(step,config,prior):return dict(self.call(step,config,prior),person_id='other-person')
        with self.assertRaises(ValueError):self.execute(changed)
        self.assertEqual(self.calls,['user'])
    def test_missing_configuration_precedes_state_and_network_writes(self):
        with self.assertRaises(ValueError):self.execute(config={'binding':'missing'})
        self.assertFalse(self.directory.exists());self.assertFalse(self.calls)
    def test_mismatched_owner_and_unknown_response_fields_refused(self):
        for change in [{'mapping_sha256':'0'*64},{'secret':'do-not-store'},{'status':'pending'}]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.execute(lambda step,c,p:dict(self.call(step,c,p),**change))
        self.assertFalse((self.directory/'state.json').exists())
    def test_checkpoint_cannot_change_configuration_or_follow_link(self):
        self.execute();state=self.directory/'state.json';state.write_text('{"mapping_sha256":"wrong","steps":{}}')
        with self.assertRaises(ValueError):self.execute()
        state.unlink();state.symlink_to(Path(self.temp.name)/'outside')
        with self.assertRaises(OSError):self.execute()
