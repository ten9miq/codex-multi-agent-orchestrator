# レポート

収集後に実行します。

```powershell
python "$env:USERPROFILE\.codex\metrics\report.py"
python "$env:USERPROFILE\.codex\metrics\report.py" --days 7
```

| option | 内容 | 既定値 |
|---|---|---|
| `--input PATH` | Metrics JSONL | `~/.codex/metrics/routing-metrics.jsonl` |
| `--days N` | 集計する直近日数（1以上） | `30` |
| `--weights PATH` | 重み付きコスト設定 JSON | `~/.codex/metrics/cost-weights.json` |

入力がなければ `collect.py` の実行を案内します。壊れた JSONL 行は数だけ表示して、読めた行から集計します。

## 出力セクション

| セクション | 項目の意味 |
|---|---|
| 冒頭 | `Rootタスク数` は Root 行数、`全モデルturn数` は対象期間の Root/child を含む行数、`解析不能JSONL行` は無視した入力行数。 |
| 初期ルート | Root の `initial_route` 分布。Root がなければその旨を表示。 |
| 品質 | `完了率` は Root の `status=COMPLETE`、`初回完遂率` は `first_pass_success`、`即時手戻り候補率` は heuristic の `possible_immediate_rework`、`検証失敗率` は `verification=FAIL` の比率。即時手戻りは確定値ではない。 |
| Agent返却結果サイズ | assistant 結果と `USER_RESULT` の推定 token（文字数÷4）の平均・中央値・P90・最大。`USER_RESULT` がない既存 rollout は0。 |
| 昇格 | `Terra → Sol` は初期 Route が `WORKER_TERRA` の Root のうち `escalation_count >= 1`、`Sol → Astra` は初期 Route が `CONTROLLER_SOL` の Root のうち `final_route=CONTROLLER_ASTRA`。分子/分母も表示。 |
| モデル別使用量 | 実効 model ごとの turn 数、input、cached input、output、reasoning、total token。total の多い順。 |
| タスク単位token分布 | `(root_thread_id, root_turn_id)` ごとに Root と帰属 subagent の total token を合算し、平均・中央値・P90を表示。 |
| Coordination / 待機 | `キャッシュ入力比率` は cached input / input、`wait/status系tool call` は call 数、`status-only token` はその token と全 total に占める割合。 |
| 重み付きコスト | 設定済みなら全 turn の合計とタスク平均、使用した設定パス。未設定なら無効と表示。 |

## 重み付きコスト

初期状態の `cost-weights.json` は全 model が `null` なので無効です。値は100万 token あたりの任意 weight です。

```json
{
  "models": {
    "gpt-5.6-luna": {"input": 1.0, "cached_input": 0.1, "output": 6.0, "reasoning": 6.0},
    "gpt-5.6-terra": {"input": 2.5, "cached_input": 0.25, "output": 15.0, "reasoning": 15.0}
  }
}
```

`weighted cost` は API料金または Codex subscription credit そのものとは限りません。比較用の相対 weight です。
