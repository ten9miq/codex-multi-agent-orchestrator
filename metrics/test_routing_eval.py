"""Offline policy and scoring tests; no model invocation or semantic-live claim."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from routing_eval import DEFAULT_FIXTURES, evaluate, expected_route, load_fixtures, score_run, usage_summary


class RoutingEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = load_fixtures(DEFAULT_FIXTURES)
        cls.cases = {case['id']: case for case in cls.fixtures['cases']}

    def run_record(self, case='D1', route='DIRECT_LUNA', role=None, policy='phase3'):
        return dict(run_id='example-1', case_id=case, policy=policy, actual_initial_route=route,
                    actual_spawned_role=role, route_evidence=['synthetic:root-routing-event'],
                    spawn_evidence=['synthetic:complete-tree'], root_tool_calls=0, root_file_access=False,
                    completion=dict(status='COMPLETE', acceptance_met=True, evidence=['synthetic:artifact-check'],
                                    verification='PASS', verification_evidence=['synthetic:test-output']),
                    agent_tree_complete=True, task_wall_seconds=3, timing_evidence=['synthetic:start/end'],
                    agents=[dict(session_id='root', parent_session_id=None, input_tokens=10,
                                 cached_input_tokens=2, output_tokens=4, elapsed_seconds=3,
                                 evidence=['synthetic:usage-event'])])

    def test_existing_nine_cases_remain_initial_three_routes_in_phase3(self):
        for id in ('D1','D2','S1','S2','S3','W1','W2','W3','C1'):
            expected = 'DIRECT_LUNA' if id == 'D1' else 'CONTROLLER_SOL'
            self.assertEqual(expected_route(self.cases[id]['feature_card'], 'phase3'), expected)

    def test_bounded_scout_whole_task_positive_and_negative_pairs(self):
        for id in ('S4','S6'):
            self.assertEqual(expected_route(self.cases[id]['feature_card'], 'phase4'), 'SCOUT_LUNA')
            self.assertEqual(expected_route(self.cases[id]['feature_card'], 'phase3'), 'CONTROLLER_SOL')
        for id in ('S1','S2','S3','S5','S7','S8'):
            self.assertEqual(expected_route(self.cases[id]['feature_card'], 'phase4'), 'CONTROLLER_SOL')

    def test_phase4_explicitly_expands_hard_reasoning_boundary(self):
        self.assertEqual(expected_route(self.cases['A5']['feature_card'], 'phase4'), 'CONTROLLER_ASTRA')
        self.assertEqual(expected_route(self.cases['A2']['feature_card'], 'phase4'), 'CONTROLLER_SOL')
        self.assertEqual(expected_route(self.cases['A5']['feature_card'], 'current'), 'CONTROLLER_SOL')

    def test_scout_blocker_is_not_automatic_model_escalation(self):
        run=self.run_record('S4','SCOUT_LUNA','scout','phase4')
        run['completion'].update(status='BLOCKED',acceptance_met=False,verification='NOT_RUN',
                                 evidence=['synthetic:network-denied'])
        result=score_run(self.cases['S4'],run)
        self.assertTrue(result['route_match'])
        self.assertFalse(result['completion_proven'])
        self.assertEqual(result['escalation_events'],[])
        self.assertIsNone(result['observed_final_route'])

    def test_scout_role_preserves_leaf_scope_and_escalation(self):
        import tomllib
        role=tomllib.loads((Path(__file__).parents[1]/'agents/scout.toml').read_text())
        instructions=role['developer_instructions']
        self.assertIn('COMPLETE|ESCALATE_SOL|BLOCKED', instructions)
        self.assertIn('CONTROLLER_LEAF', instructions)
        self.assertIn('EXPLICIT', instructions)
        self.assertIn('ROOT_AUTO', instructions)
        self.assertEqual(role['sandbox_mode'], 'read-only')

    def test_hard_reasoning_and_ordinary_design_pair(self):
        self.assertEqual(expected_route(self.cases['A1']['feature_card'], 'phase3'), 'CONTROLLER_ASTRA')
        self.assertEqual(expected_route(self.cases['A2']['feature_card'], 'phase3'), 'CONTROLLER_SOL')

    def test_phase3_preserves_consequence_boundary(self):
        self.assertEqual(expected_route(self.cases['A5']['feature_card'], 'phase3'), 'CONTROLLER_SOL')
        self.assertEqual(expected_route(self.cases['A1']['feature_card'], 'phase3'), 'CONTROLLER_ASTRA')

    def test_initial_route_is_not_later_escalation(self):
        run=self.run_record('A3','CONTROLLER_SOL','controller_sol')
        run.update(actual_final_route='CONTROLLER_ASTRA', final_route_evidence=['synthetic:final-event'],
                   escalation_events=[dict(from_route='CONTROLLER_SOL',to_route='CONTROLLER_ASTRA',
                                           evidence=['synthetic:escalation-event'])])
        result=score_run(self.cases['A3'],run)
        self.assertTrue(result['route_match'])
        self.assertEqual(result['observed_final_route'],'CONTROLLER_ASTRA')
        run.update(actual_initial_route='CONTROLLER_ASTRA',actual_spawned_role='controller_astra')
        self.assertFalse(score_run(self.cases['A3'],run)['route_match'])

    def test_unknown_or_missing_evidence_is_not_confidence(self):
        for id in ('W1','S8'):
            self.assertEqual(expected_route(self.cases[id]['feature_card'], 'phase3'), 'CONTROLLER_SOL')
        card = copy.deepcopy(self.cases['D1']['feature_card'])
        card['scope']['evidence'] = ''
        self.assertEqual(expected_route(card, 'phase3'), 'CONTROLLER_SOL')
        card['confidence'] = .99
        with self.assertRaises(ValueError):
            expected_route(card, 'phase3')

    def test_all_reviewed_policy_expectations(self):
        for case in self.fixtures['cases']:
            for policy, accepted in case['acceptable_routes'].items():
                with self.subTest(case=case['id'],policy=policy):
                    self.assertIn(expected_route(case['feature_card'], policy), accepted)

    def test_actual_spawn_role_not_model_name(self):
        run = self.run_record('D2', 'CONTROLLER_SOL', 'worker_sol')
        self.assertEqual(score_run(self.cases['D2'], run)['route_classification'], 'contract_violation')
        run['actual_spawned_role']='controller_sol'
        self.assertTrue(score_run(self.cases['D2'], run)['pass'])
        del run['actual_spawned_role']
        self.assertEqual(score_run(self.cases['D2'], run)['route_classification'], 'unverified')

    def test_direct_cannot_use_tools_or_files(self):
        run = self.run_record()
        self.assertTrue(score_run(self.cases['D1'], run)['pass'])
        for field, value in [('root_tool_calls',1),('root_file_access',True)]:
            with self.subTest(field=field):
                invalid=copy.deepcopy(run); invalid[field]=value
                self.assertEqual(score_run(self.cases['D1'], invalid)['route_classification'], 'contract_violation')

    def test_downgrade_and_overroute_candidates(self):
        under = self.run_record('A1','CONTROLLER_SOL','controller_sol')
        over = self.run_record('D1','CONTROLLER_SOL','controller_sol')
        self.assertEqual(score_run(self.cases['A1'],under)['route_classification'],'dangerous_downgrade')
        self.assertEqual(score_run(self.cases['D1'],over)['route_classification'],'overroute_candidate')
        accepted = self.run_record('A4','CONTROLLER_ASTRA','controller_astra')
        self.assertTrue(score_run(self.cases['A4'],accepted)['route_match'])

    def test_completion_requires_acceptance_and_evidence(self):
        for key,value in [('status','BLOCKED'),('acceptance_met',False),('evidence',[]),
                          ('verification','FAIL'),('verification_evidence',[])]:
            run=self.run_record('W2','CONTROLLER_SOL','controller_sol')
            run['completion'][key]=value
            self.assertFalse(score_run(self.cases['W2'],run)['completion_proven'])
        run['completion'].update(verification='NOT_RUN')
        self.assertFalse(score_run(self.cases['W2'],run)['completion_proven'])

    def test_root_children_usage_and_parallel_time_are_distinct(self):
        run=self.run_record()
        child=copy.deepcopy(run['agents'][0]); child.update(session_id='child',parent_session_id='root',elapsed_seconds=2)
        run['agents'].append(child)
        usage=usage_summary(run)
        self.assertEqual(usage['input_tokens'],20)
        self.assertEqual(usage['cached_input_tokens'],4)
        self.assertEqual(usage['agent_seconds'],5)
        self.assertEqual(usage['task_wall_seconds'],3)
        child['input_tokens']=None
        self.assertIsNone(usage_summary(run)['input_tokens'])
        self.assertEqual(usage_summary(run)['input_tokens_observed_subtotal'],10)
        run['agent_tree_complete']=False
        self.assertIsNone(usage_summary(run)['output_tokens'])

    def test_missing_sources_and_cyclic_tree_are_not_measured(self):
        run=self.run_record(); run['agents'][0]['evidence']=[]
        self.assertIsNone(usage_summary(run)['input_tokens'])
        run=self.run_record()
        for id,parent in [('a','b'),('b','a')]:
            node=copy.deepcopy(run['agents'][0]); node.update(session_id=id,parent_session_id=parent)
            run['agents'].append(node)
        self.assertFalse(usage_summary(run)['tree_complete'])

    def test_synthetic_never_becomes_live_and_missing_cases_visible(self):
        observations=dict(schema_version=1,fixture_version=self.fixtures['fixture_version'],
                          data_kind='synthetic',runs=[self.run_record()])
        report=evaluate(self.fixtures,observations)
        self.assertFalse(report['live_semantic_validation'])
        self.assertIn('D2',report['policies']['phase3']['cases_missing'])
        observations['runs'][0]['data_kind']='recorded_live'
        with self.assertRaises(ValueError): evaluate(self.fixtures,observations)

    def test_fixture_version_and_duplicate_run_are_rejected(self):
        observations=dict(schema_version=1,fixture_version='wrong',data_kind='synthetic',runs=[])
        with self.assertRaises(ValueError): evaluate(self.fixtures,observations)
        observations['fixture_version']=self.fixtures['fixture_version']
        observations['runs']=[self.run_record(),self.run_record()]
        with self.assertRaises(ValueError): evaluate(self.fixtures,observations)

    def test_cli_without_observations_only_reports_expectations(self):
        output=subprocess.check_output([sys.executable,str(Path(__file__).with_name('routing_eval.py'))],text=True)
        report=json.loads(output)
        self.assertEqual(report['data_kind'],'policy_expectations_only')
        self.assertFalse(report['live_semantic_validation'])


if __name__ == '__main__': unittest.main()
