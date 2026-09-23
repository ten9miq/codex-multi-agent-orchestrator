from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import smoke
from collect import mark_rework
from rollout_reader import parse_rollout_turns, parse_session_summary


def write_rollout(path: Path, values: list[dict]) -> None:
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def rollout_values(*, include_context: bool = True) -> list[dict]:
    values = [
        {"type": "session_meta", "payload": {"id": "root-a", "source": {}}},
        {
            "type": "turn_context",
            "payload": {"model": "gpt-6-luna", "effort": "medium", "multi_agent_version": "v2"},
        },
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"type": "task_started", "turn_id": "turn-a", "model_context_window": 800},
        },
    ]
    if include_context:
        for tokens in (120, 300):
            values.append(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": tokens * 2},
                            "last_token_usage": {"total_tokens": tokens},
                            "model_context_window": 1000,
                        },
                    },
                }
            )
    values.append(
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:01Z",
            "payload": {"type": "task_complete", "turn_id": "turn-a"},
        }
    )
    return values


def assistant_message(text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def user_message(text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


class ContextObservabilityTests(unittest.TestCase):
    def test_root_model_alone_does_not_prove_a_route(self) -> None:
        for model in ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-sol", "gpt-6-astra", "gpt-6-luna"):
            with self.subTest(model=model):
                values = rollout_values(include_context=False)
                values[1]["payload"]["model"] = model
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / f"rollout-{model}.jsonl"
                    write_rollout(path, values)
                    turn = parse_rollout_turns(path)[0]

                self.assertEqual(turn.model, model)
                self.assertEqual(turn.initial_route, "UNKNOWN")
                self.assertEqual(turn.final_route, "UNKNOWN")
                self.assertIsNone(turn.initial_route_source)
                self.assertIsNone(turn.final_route_source)

    def test_consecutive_root_turn_rework_classes_preserve_legacy_boolean(self) -> None:
        cases = [
            ("前の結果は違います。直してください。", "MODEL_CORRECTION", True),
            ("追加でREADMEも更新してください。", "USER_FOLLOWUP", False),
            ("もう一度確認してください。", "UNKNOWN", False),
            ("ありがとうございました。", "NONE", False),
        ]
        for text, expected_class, expected_legacy in cases:
            with self.subTest(text=text):
                values = rollout_values(include_context=False)
                values.extend([
                    user_message(text),
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:05:00Z",
                        "payload": {"type": "task_started", "turn_id": "turn-b"},
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:05:01Z",
                        "payload": {"type": "task_complete", "turn_id": "turn-b"},
                    },
                ])
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "rollout-rework.jsonl"
                    write_rollout(path, values)
                    turns = parse_rollout_turns(path)

                self.assertEqual(len(turns), 2)
                self.assertEqual(turns[1].user_rework_class, expected_class)
                mark_rework(turns)
                self.assertEqual(turns[0].rework_class, expected_class)
                self.assertEqual(turns[0].possible_immediate_rework, expected_legacy)
                self.assertIn("rework_class", turns[0].public())
                self.assertNotIn("user_rework_class", turns[0].public())

    def test_effective_context_uses_last_token_snapshot_and_tracks_peak(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-context.jsonl"
            write_rollout(path, rollout_values())

            summary = parse_session_summary(path)
            turns = parse_rollout_turns(path)

        self.assertEqual(summary.context_window, 1000)
        self.assertEqual(summary.context_tokens, 300)
        self.assertEqual(summary.context_peak_tokens, 300)
        self.assertEqual(summary.context_usage_pct, 30.0)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].context_window, 1000)
        self.assertEqual(turns[0].context_peak_tokens, 300)
        self.assertEqual(turns[0].context_peak_usage_pct, 30.0)
        self.assertEqual(turns[0].effective_route, turns[0].final_route)
        self.assertEqual(turns[0].public()["context_usage_pct"], 30.0)
        self.assertEqual(turns[0].cache_record()["context_peak_tokens"], 300)

    def test_missing_context_is_nonfatal_even_with_expect(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            write_rollout(sessions / "rollout-context.jsonl", rollout_values(include_context=False))
            output = io.StringIO()
            argv = sys.argv
            try:
                sys.argv = ["smoke.py", "--sessions-root", str(sessions), "--minutes", "9999999", "--expect", "root", "--context", "--json"]
                with contextlib.redirect_stdout(output):
                    result = smoke.main()
            finally:
                sys.argv = argv

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(payload["pass"])
        self.assertEqual(payload["warnings"], 0)
        self.assertEqual(len(payload["context_warnings"]), 1)
        self.assertIsNone(payload["sessions"][0]["context_tokens"])

    def test_execution_mode_compaction_and_wait_status_classification(self) -> None:
        values = rollout_values()
        values.insert(4, {"type": "compacted", "payload": {}})
        values.insert(5, {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "write_stdin",
                "arguments": '{"chars":"continue"}',
            },
        })
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-new.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.execution_mode, "ORCHESTRATED_ROUTE")
        self.assertEqual(turn.compaction_count, 1)
        self.assertEqual(turn.compaction_context_tokens, [120])
        self.assertEqual(turn.compaction_context_windows, [1000])
        self.assertFalse(turn.status_only_turn)
        self.assertEqual(turn.wait_tool_calls, 0)

    def test_wait_only_turn_is_status_only_for_legacy_and_new_schema(self) -> None:
        values = rollout_values()
        values[1]["payload"].pop("multi_agent_version")
        values.insert(4, {
            "type": "response_item",
            "payload": {"type": "function_call", "name": "wait_agent", "arguments": "{}"},
        })
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-legacy.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.execution_mode, "LEGACY_ROOT_MODEL")
        self.assertTrue(turn.status_only_turn)
        self.assertEqual(turn.wait_tool_calls, 1)
        self.assertEqual(turn.wait_status_tokens, turn.total_tokens)

    def test_protocol_uses_only_final_assistant_or_task_complete_output(self) -> None:
        values = rollout_values(include_context=False)
        # tool引数とuser messageの文字列は、従来のall_strings走査なら誤採用した。
        values.insert(3, {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "send_message",
                "arguments": (
                    '{"prompt":"ROUTER_STATUS: COMPLETE\\nROUTER_VERIFY: PASS\\n'
                    'ROUTER_ROUTE: CONTROLLER_ASTRA\\nROUTER_RETRY: 9"}'
                ),
            },
        })
        values.insert(4, {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "ROUTER_VERIFY: PASS"}],
            },
        })
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-protocol-no-output.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.status, "COMPLETE")
        self.assertEqual(turn.verification, "UNKNOWN")
        self.assertEqual(turn.retry_count, 0)
        self.assertEqual(turn.status_source, "completion_event")
        self.assertIsNone(turn.verification_source)
        self.assertEqual(turn.final_route, "UNKNOWN")
        self.assertIsNone(turn.route_source)

    def test_gpt6_luna_without_tools_has_unobserved_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-direct.jsonl"
            write_rollout(path, rollout_values(include_context=False))
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.initial_route, "UNKNOWN")

    def test_spawned_child_role_does_not_become_root_route(self) -> None:
        values = rollout_values(include_context=False)
        values.insert(3, {
            "type": "response_item",
            "payload": {"type": "function_call", "name": "spawn_agent",
                        "call_id": "spawn-1", "arguments": json.dumps({"agent_type": "worker_sol"})},
        })
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-spawn.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.subagent_count, 1)
        self.assertEqual(turn.first_spawn_role, "worker_sol")
        self.assertEqual(turn.initial_route, "UNKNOWN")
        self.assertEqual(turn.final_route, "UNKNOWN")
        self.assertIsNone(turn.initial_route_source)
        self.assertIsNone(turn.final_route_source)

    def test_first_spawn_role_uses_first_known_named_role(self) -> None:
        values = rollout_values(include_context=False)
        values[3:3] = [
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "spawn_agent",
                            "call_id": "spawn-unknown", "arguments": json.dumps({"agent_type": "custom"})},
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "spawn_agent",
                            "call_id": "spawn-known-1", "arguments": json.dumps({"agent_type": "scout"})},
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "spawn_agent",
                            "call_id": "spawn-known-2", "arguments": json.dumps({"agent_type": "worker_sol"})},
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-first-spawn.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.first_spawn_role, "scout")
        self.assertEqual(turn.initial_route, "UNKNOWN")
        self.assertEqual(turn.final_route, "UNKNOWN")

    def test_protocol_provenance_prefers_task_complete_last_agent_message(self) -> None:
        values = rollout_values(include_context=False)
        values.insert(3, assistant_message(
            "ROUTER_STATUS: BLOCKED\nROUTER_VERIFY: FAIL\n"
            "ROUTER_ROUTE: WORKER_TERRA\nROUTER_RETRY: 2"
        ))
        values[-1]["payload"]["last_agent_message"] = (
            "ROUTER_STATUS: COMPLETE\nROUTER_VERIFY: NOT_RUN\n"
            "ROUTER_ROUTE: SCOUT_LUNA\nROUTER_RETRY: 1"
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-protocol-complete.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.status, "COMPLETE")
        self.assertEqual(turn.verification, "NOT_RUN")
        self.assertEqual(turn.retry_count, 1)
        self.assertEqual(turn.final_route, "SCOUT_LUNA")
        self.assertEqual(turn.status_source, "task_complete.last_agent_message")
        self.assertEqual(turn.verification_source, "task_complete.last_agent_message")
        self.assertEqual(turn.retry_source, "task_complete.last_agent_message")
        self.assertEqual(turn.route_source, "task_complete.last_agent_message")
        self.assertIsNone(turn.initial_route_source)
        self.assertEqual(turn.final_route_source, "task_complete.last_agent_message")

    def test_protocol_can_use_last_assistant_message_without_completion_event(self) -> None:
        values = rollout_values(include_context=False)
        values.pop()
        values.append(assistant_message(
            "ROUTER_STATUS: BLOCKED\nROUTER_VERIFY: FAIL\n"
            "ROUTER_ROUTE: CONTROLLER_SOL\nROUTER_RETRY: 3"
        ))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-protocol-assistant.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.status, "BLOCKED")
        self.assertEqual(turn.verification, "FAIL")
        self.assertEqual(turn.final_route, "CONTROLLER_SOL")
        self.assertEqual(turn.retry_count, 3)
        self.assertEqual(turn.status_source, "assistant_message")
        self.assertEqual(turn.verification_source, "assistant_message")
        self.assertEqual(turn.route_source, "assistant_message")
        self.assertIsNone(turn.initial_route_source)
        self.assertEqual(turn.final_route_source, "assistant_message")
        self.assertEqual(turn.retry_source, "assistant_message")

    def test_completion_event_fallback_does_not_override_protocol_status(self) -> None:
        for status in ("BLOCKED", "ESCALATE_SOL"):
            with self.subTest(status=status):
                values = rollout_values(include_context=False)
                values.insert(3, assistant_message(f"ROUTER_STATUS: {status}"))
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / f"rollout-protocol-{status.lower()}.jsonl"
                    write_rollout(path, values)
                    turn = parse_rollout_turns(path)[0]

                self.assertEqual(turn.status, status)
                self.assertEqual(turn.status_source, "assistant_message")

    def test_incomplete_turn_without_protocol_remains_unknown(self) -> None:
        values = rollout_values(include_context=False)
        values.pop()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-incomplete.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.status, "UNKNOWN")
        self.assertIsNone(turn.status_source)

    def test_completion_without_timestamp_does_not_get_fallback_status(self) -> None:
        values = rollout_values(include_context=False)
        values[-1].pop("timestamp")
        values[-1]["payload"]["last_agent_message"] = "ROUTER_STATUS: BLOCKED"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-completion-no-timestamp.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.status, "BLOCKED")
        self.assertEqual(turn.status_source, "task_complete.last_agent_message")

    def test_scout_not_run_requires_explicit_protocol(self) -> None:
        values = rollout_values(include_context=False)
        values[0]["payload"]["source"] = {
            "subagent": {"parent_thread_id": "root-a"},
            "agent_role": "scout",
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-scout-no-protocol.jsonl"
            write_rollout(path, values)
            turn = parse_rollout_turns(path)[0]

        self.assertEqual(turn.verification, "UNKNOWN")
        self.assertIsNone(turn.verification_source)


if __name__ == "__main__":
    unittest.main()
