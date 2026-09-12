# `routing-metrics.jsonl`

1行が1 turn の machine-readable JSON です。field 名は将来の集計・互換性のため英語のまま固定します。

| field | 内容 |
|---|---|
| `timestamp` / `end_timestamp` | turn の開始・終了時刻（UTC の元値） |
| `thread_id` / `parent_thread_id` | session/thread ID と親 thread ID |
| `root_thread_id` / `root_turn_id` | 帰属する Root thread / Root turn |
| `turn_id` | turn ID |
| `is_root` | Root か |
| `agent_role` | `scout` / `worker_terra` 等の role |
| `route` / `initial_route` / `final_route` / `effective_route` | 互換用の最終 Route / 最初の Route / 最終 Route / レポートで用いる実効 Route。`effective_route` は `final_route` の後方互換 alias。 |
| `model` / `reasoning_effort` / `multi_agent_version` | 実効 model、effort、V1/V2 |
| `input_tokens` / `cached_input_tokens` / `output_tokens` / `reasoning_tokens` / `total_tokens` | turn の token 内訳 |
| `context_window` / `context_tokens` / `context_peak_tokens` | `last_token_usage` から得た実効context window、現在値、turn内peak。累積tokenではない。 |
| `context_usage_pct` / `context_peak_usage_pct` | context windowに対する現在値・peakの使用率（不明は `null`） |
| `status` / `verification` | `COMPLETE` 等の状態、`PASS` / `FAIL` / `NOT_RUN` 等の検証結果 |
| `escalation_count` / `retry_count` / `subagent_count` | 昇格、retry、spawn した subagent の回数 |
| `duration_seconds` | turn 時間 |
| `wait_tool_calls` / `status_only_turn` / `wait_status_tokens` | wait/status 系 call 数、状態確認だけの turn か、その token |
| `possible_immediate_rework` / `first_pass_success` | 即時手戻り候補、初回完遂判定 |
| `result_chars` / `user_result_chars` | assistant 結果、USER_RESULT 部分の文字数 |
| `result_estimated_tokens` / `user_result_estimated_tokens` | 各文字数÷4の tokenizer 非依存の比較用概算 token |

## session・thread・turn・Root・Child

- **session/thread**: `thread_id` がその turn の session/thread。親 agent は `parent_thread_id` で示します。
- **turn**: 同じ thread の個別の処理単位で、`turn_id` と開始・終了時刻を持ちます。
- **Root**: `is_root=true` の turn。Root task 自身の `thread_id` / `turn_id` が、原則 `root_thread_id` / `root_turn_id` になります。
- **Child / Grandchild**: Root に属する child は親 thread と root ID を保持します。report は同じ `(root_thread_id, root_turn_id)` の Root と全 child usage を1 task に集計します。
- **未帰属**: 親 ID が session 横断で一意に特定できない場合は、別 session と推測で接続しません。

`result_estimated_tokens` は実際の課金・usage token ではありません。実 token の比較には token 内訳 field を使用します。
