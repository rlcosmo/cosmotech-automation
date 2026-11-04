import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pipeline_bridge', Path(__file__).resolve().parents[1] / 'ops/pipelines/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class PipelineEvidenceTests(unittest.TestCase):
    def test_validation_rejects_paths_and_shell_payloads(self):
        for app, run, sha, stage in [('../bad', '123', 'a'*40, 'tests'), ('orders-api', '12;id', 'a'*40, 'tests'),
                                     ('orders-api', '12', 'main', 'tests'), ('orders-api', '12', 'a'*40, 'anything')]:
            with self.subTest(app=app, run=run, sha=sha, stage=stage), self.assertRaises(ValueError):
                bridge.validate(app, run, '1', sha, stage, 'success')

    def test_duplicate_and_late_running_event_do_not_regress_success(self):
        value = {'app': 'orders-api', 'state': 'running', 'stages': {}}
        bridge.evolve(value, 'tests', 'success', 'now')
        bridge.evolve(value, 'tests', 'running', 'later')
        self.assertEqual(value['stages']['tests']['state'], 'success')

    def test_only_real_success_for_every_stage_completes_pipeline(self):
        value = {'app': 'orders-worker', 'state': 'running', 'stages': {}}
        for stage in bridge.STAGES['orders-worker'][:-1]:
            bridge.evolve(value, stage, 'success', 'now')
        self.assertEqual(value['state'], 'running')
        bridge.evolve(value, 'health', 'success', 'now')
        self.assertEqual(value['state'], 'success')

    def test_failure_cannot_be_rewritten_by_late_success(self):
        value = {'app': 'orders-worker', 'state': 'running', 'stages': {}}
        bridge.evolve(value, 'tests', 'failure', 'now')
        bridge.evolve(value, 'tests', 'success', 'later')
        self.assertEqual(value['state'], 'failure')
        self.assertEqual(value['stages']['tests']['state'], 'failure')

    def test_duplicate_delivery_creates_one_kestra_execution(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'ROOT', Path(directory)), patch.object(bridge, 'request', return_value={'id':'real-execution'}) as request:
            first = bridge.record('orders-api','123','1','a'*40,'tests','running')
            second = bridge.record('orders-api','123','1','a'*40,'tests','success')
            self.assertEqual(first['execution_id'], second['execution_id'])
            self.assertEqual(request.call_count, 1)

    def test_ambiguous_dispatch_never_silently_creates_duplicate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'ROOT', Path(directory)), patch.object(bridge, 'request', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                bridge.record('orders-api','123','1','a'*40,'tests','running')
            with self.assertRaisesRegex(RuntimeError,'Ambiguous'):
                bridge.record('orders-api','123','1','a'*40,'tests','running')

    def test_untracked_older_release_does_not_fabricate_a_source_run(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'ROOT', Path(directory)):
            self.assertTrue(bridge.record('orders-api','0','1','b'*40,'rollout','success')['untracked_source'])

    def test_commit_mismatch_rejects_reusing_run_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'ROOT', Path(directory)), patch.object(bridge, 'request', return_value={'id':'real-execution'}):
            bridge.record('orders-api','123','1','a'*40,'tests','running')
            with self.assertRaisesRegex(ValueError,'Commit mismatch'):
                bridge.record('orders-api','123','1','b'*40,'tests','success')
            value=json.loads((Path(directory)/'orders-api-123-1.json').read_text())
            self.assertEqual(value['sha'], 'a'*40)
