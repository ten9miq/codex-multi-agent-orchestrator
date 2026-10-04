"""Versioned, offline routing-policy checks. Never calls/spawns a model or tools.

Feature cards and acceptable routes are reviewable policy expectations, not a
runtime dispatcher or evidence that a natural-language router followed policy.
Recorded outcomes must be supplied explicitly; synthetic and real runs never mix.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
POLICIES = ('current', 'phase3', 'always_sol')
AXES = {
    'scope': {'conversation', 'bounded_retrieval', 'localized_change', 'multicomponent', 'unknown'},
    'method': {'exact_text', 'direct_retrieval', 'exact_transform', 'semantic', 'investigate', 'unknown'},
    'context': {'conversation', 'specified_sources', 'unbounded', 'unknown'},
    'reasoning': {'none', 'lookup', 'design', 'diagnosis', 'hard', 'unknown'},
    'verification': {'not_needed', 'readback', 'existing_deterministic', 'new_check', 'unknown'},
    'consequence': {'low', 'reversible_local', 'protected', 'high_impact', 'unknown'},
}
ROLES = {
    'DIRECT_LUNA': None,
    'SCOUT_LUNA': 'scout',
    'WORKER_LUNA': 'worker_luna',
    'WORKER_SOL': 'worker_sol',
    'CONTROLLER_SOL': 'controller_sol',
    'CONTROLLER_ASTRA': 'controller_astra',
}
# Effort is deliberately not ranked. This order only labels policy route misses;
# overroute is a candidate, never a claim of demonstrated quality-adjusted waste.
ROUTE_RANK = {'DIRECT_LUNA': 0, 'SCOUT_LUNA': 1, 'WORKER_LUNA': 2,
              'WORKER_SOL': 3, 'CONTROLLER_SOL': 3, 'CONTROLLER_ASTRA': 4}
DEFAULT_FIXTURES = Path(__file__).parent / 'fixtures' / 'routing' / 'v1.json'


def nonempty_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(s, str) and s.strip() for s in value)


def validate_card(card: dict) -> dict[str, str]:
    if set(card) != set(AXES) | {'unknowns'}:
        raise ValueError('feature card must contain exactly the six axes and unknowns')
    values = {}
    for axis, allowed in AXES.items():
        cell = card[axis]
        if not isinstance(cell, dict) or set(cell) != {'value', 'evidence'}:
            raise ValueError(f'{axis}: requires value and evidence (no confidence score)')
        if cell['value'] not in allowed or not isinstance(cell['evidence'], str):
            raise ValueError(f'{axis}: invalid value/evidence')
        values[axis] = cell['value'] if cell['evidence'].strip() else 'unknown'
    if not isinstance(card['unknowns'], list) or not all(isinstance(v, str) and v.strip() for v in card['unknowns']):
        raise ValueError('unknowns must be a list of unresolved facts')
    return values


def expected_route(card: dict, policy: str) -> str:
    """An offline expectation for ROOT_AUTO only; does not execute routing."""
    if policy not in POLICIES:
        raise ValueError(f'unsupported policy: {policy}')
    values = validate_card(card)
    if policy == 'always_sol':
        return 'CONTROLLER_SOL'
    if card['unknowns'] or 'unknown' in values.values():
        return 'CONTROLLER_SOL'
    if values['consequence'] == 'high_impact' and values['reasoning'] in {'hard', 'design', 'diagnosis'}:
        return 'CONTROLLER_ASTRA'
    if values == dict(scope='conversation', method='exact_text', context='conversation',
                      reasoning='none', verification='not_needed', consequence='low'):
        return 'DIRECT_LUNA'
    return 'CONTROLLER_SOL'


def load_fixtures(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != SCHEMA_VERSION or not isinstance(data.get('fixture_version'), str):
        raise ValueError('unsupported fixture schema/version')
    ids = set()
    for case in data['cases']:
        if case['id'] in ids:
            raise ValueError('duplicate fixture id')
        ids.add(case['id'])
        validate_card(case['feature_card'])
        if case['routing_mode'] != 'ROOT_AUTO':
            raise ValueError('these fixtures evaluate ROOT_AUTO, not leaf/explicit contracts')
        for policy in POLICIES:
            accepted = case['acceptable_routes'][policy]
            if not accepted or any(route not in ROLES for route in accepted):
                raise ValueError('invalid acceptable routes')
            if expected_route(case['feature_card'], policy) not in accepted:
                raise ValueError(f'{case["id"]}: policy expectation not in reviewed acceptable set')
    return data


def measured_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def usage_summary(run: dict) -> dict:
    """Root + all descendant usage, with missing values preserved as unknown.

    Child count is asserted by the recorder and needs independent review. No
    inference from model names or result envelopes can prove tree completeness.
    """
    agents = run.get('agents', [])
    if not isinstance(agents, list):
        raise ValueError('agents must be a list')
    ids = [agent.get('session_id') for agent in agents]
    complete_tree = (run.get('agent_tree_complete') is True and
                     sum(agent.get('parent_session_id') is None for agent in agents) == 1 and
                     all(isinstance(v, str) and v.strip() for v in ids) and len(set(ids)) == len(ids) and
                     all(agent.get('parent_session_id') is None or agent['parent_session_id'] in ids for agent in agents))
    # Reject disconnected cycles even when every named parent exists.
    if complete_tree:
        parents = {agent['session_id']: agent.get('parent_session_id') for agent in agents}
        for node in parents:
            visited = set()
            while node is not None:
                if node in visited:
                    complete_tree = False
                    break
                visited.add(node)
                node = parents[node]
    result = {'agent_count': len(agents), 'tree_complete': complete_tree}
    for field in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'elapsed_seconds'):
        numbers = [agent.get(field) for agent in agents]
        observed = [value for value in numbers if measured_number(value)]
        has_sources = all(nonempty_strings(agent.get('evidence')) for agent in agents)
        total = sum(observed) if complete_tree and has_sources and len(observed) == len(agents) else None
        name = 'agent_seconds' if field == 'elapsed_seconds' else field
        result[name] = total
        result[f'{name}_observed_subtotal'] = sum(observed)
    wall = run.get('task_wall_seconds')
    result['task_wall_seconds'] = wall if measured_number(wall) and nonempty_strings(run.get('timing_evidence')) else None
    return result


def score_run(case: dict, run: dict) -> dict:
    policy = run['policy']
    if policy not in POLICIES:
        raise ValueError(f'unsupported policy: {policy}')
    accepted = case['acceptable_routes'][policy]
    route = run.get('actual_initial_route')
    if route not in ROLES:
        raise ValueError('actual_initial_route must be an observed Route ID')
    route_supported = nonempty_strings(run.get('route_evidence'))
    actual_role = run.get('actual_spawned_role')
    role_known = 'actual_spawned_role' in run and nonempty_strings(run.get('spawn_evidence'))
    role_matches = role_known and actual_role == ROLES[route]
    direct_legal = route != 'DIRECT_LUNA' or (run.get('root_tool_calls') == 0 and
                                              run.get('root_file_access') is False)
    route_match = route in accepted and route_supported and role_matches and direct_legal
    if not route_supported or not role_known:
        classification = 'unverified'
    elif not role_matches or not direct_legal:
        classification = 'contract_violation'
    elif route in accepted:
        classification = 'acceptable'
    elif ROUTE_RANK[route] < min(ROUTE_RANK[item] for item in accepted):
        classification = 'dangerous_downgrade'
    elif ROUTE_RANK[route] > max(ROUTE_RANK[item] for item in accepted):
        classification = 'overroute_candidate'
    else:
        classification = 'route_mismatch'
    completion = run.get('completion', {})
    complete = (completion.get('status') == 'COMPLETE' and completion.get('acceptance_met') is True and
                nonempty_strings(completion.get('evidence')) and
                completion.get('verification') in {'PASS', 'NOT_RUN'} and
                (not case['requires_verification'] or completion.get('verification') == 'PASS'))
    if completion.get('verification') == 'PASS' and not nonempty_strings(completion.get('verification_evidence')):
        complete = False
    return {
        'case_id': case['id'], 'policy': policy, 'run_id': run['run_id'],
        'acceptable_routes': accepted, 'actual_initial_route': route,
        'actual_spawned_role': actual_role, 'route_classification': classification,
        'route_match': bool(route_match), 'completion_proven': bool(complete),
        'completion_evidence': completion.get('evidence', []),
        'verification_evidence': completion.get('verification_evidence', []),
        'pass': bool(route_match and complete), 'usage': usage_summary(run),
        # Final/escalated routes are separate observations, never initial-route
        # acceptable-set evidence. Missing evidence stays unknown.
        'observed_final_route': run.get('actual_final_route') if
            run.get('actual_final_route') in ROLES and nonempty_strings(run.get('final_route_evidence')) else None,
        'escalation_events': [event for event in run.get('escalation_events', []) if
            event.get('from_route') in ROLES and event.get('to_route') in ROLES and
            nonempty_strings(event.get('evidence'))],
    }


def evaluate(fixtures: dict, observations: dict) -> dict:
    if observations.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('unsupported observation schema')
    if observations.get('fixture_version') != fixtures['fixture_version']:
        raise ValueError('fixture version mismatch')
    kind = observations.get('data_kind')
    if kind not in {'synthetic', 'recorded_live'}:
        raise ValueError('data_kind must be synthetic or recorded_live')
    cases = {case['id']: case for case in fixtures['cases']}
    results, ids = [], set()
    for run in observations['runs']:
        if run.get('data_kind', kind) != kind:
            raise ValueError('never mix synthetic and recorded_live observations')
        if not isinstance(run.get('run_id'), str) or not run['run_id'].strip() or run['run_id'] in ids:
            raise ValueError('run_id must be nonempty and unique')
        ids.add(run['run_id'])
        if run['case_id'] not in cases:
            raise ValueError('unknown case_id')
        results.append(score_run(cases[run['case_id']], run))
    groups = {}
    for policy in POLICIES:
        items = [result for result in results if result['policy'] == policy]
        observed_cases = {item['case_id'] for item in items}
        groups[policy] = {
            'runs': len(items), 'cases_observed': len(observed_cases),
            'cases_missing': sorted(set(cases) - observed_cases),
            'route_matches': sum(item['route_match'] for item in items),
            'completion_proven': sum(item['completion_proven'] for item in items),
            'dangerous_downgrades': sum(item['route_classification'] == 'dangerous_downgrade' for item in items),
            'overroute_candidates': sum(item['route_classification'] == 'overroute_candidate' for item in items),
            'passed': sum(item['pass'] for item in items),
        }
    return {'schema_version': SCHEMA_VERSION, 'fixture_version': fixtures['fixture_version'],
            'data_kind': kind, 'live_semantic_validation': False if kind == 'synthetic' else 'requires_independent_review',
            'policies': groups, 'runs': results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument('--observations', type=Path, help='explicit recorded JSON; never scans sessions')
    args = parser.parse_args(argv)
    try:
        fixtures = load_fixtures(args.fixtures)
        if args.observations:
            report = evaluate(fixtures, json.loads(args.observations.read_text(encoding='utf-8')))
        else:
            report = {'schema_version': SCHEMA_VERSION, 'fixture_version': fixtures['fixture_version'],
                      'data_kind': 'policy_expectations_only', 'live_semantic_validation': False,
                      'cases': [{'id': case['id'], 'expectations': {policy: expected_route(case['feature_card'], policy)
                                 for policy in POLICIES}, 'acceptable_routes': case['acceptable_routes']}
                                for case in fixtures['cases']]}
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0  # Successful offline scoring, not a claim that all cases passed.
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f'Invalid evaluation input: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
