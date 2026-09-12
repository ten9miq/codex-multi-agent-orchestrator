# Wait / Status Polling Policy

## 目的

Multi-Agentのchild完了を待つ処理で、短いtimeoutが繰り返されると、状態変化がないのに親turnが再評価され、tokenと処理時間を消費することがあります。

このリポジトリでは、wait/status pollingを「頻繁に確認する」よりも「長めに待ち、状態変化があったときだけ再開する」方針にします。

## 設定値

`config.example.toml`では、Codex CLI 0.154.0のV2既定値を次のように上書きしています。

| Parameter | Repository value | 換算 | Codex V2 default | 役割 |
|---|---:|---:|---:|---|
| `min_wait_timeout_ms` | `1200000` | 20分 | `10000` ms（10秒） | 指定可能な最小wait時間 |
| `default_wait_timeout_ms` | `1200000` | 20分 | `30000` ms（30秒） | timeout未指定時のwait時間 |
| `max_wait_timeout_ms` | `1500000` | 25分 | `3600000` ms（60分） | 指定可能な最大wait時間 |

既定値を無条件に長くすることが目的ではありません。長時間実行されるchildを待つ通常のController処理で、30秒ごとの再サンプリングを減らすための設定です。

## なぜ20分なのか

この値は「20分待てば必ず完了する」という意味ではありません。

```text
短いwait
  → 状態変化なし
  → 親が再びmodelを呼ぶ
  → 同じcontextを再評価
  → token増加

長いwait
  → childの完了または意味のある状態変化まで待つ
  → 親の不要なstatus-only turnを減らす
```

20分は、通常のsubagent実装・調査を一度のwaitで待てる余裕と、異常時に無期限で止まらないことの折衷値です。25分は明示的に長く待ちたい場合の上限としています。

## 注意点

- wait timeoutを長くしても、childが失敗・停止した場合の復旧そのものは行いません。
- UIの即時表示が必要な処理では、長いtimeoutが状態表示の遅延に見えることがあります。
- `min_wait_timeout_ms`が1200000msのため、モデルがそれ未満のtimeoutを指定してもruntime側で制約されます。
- `max_depth`やagent数の制約とは別の設定です。polling間隔を長くしてもagent増殖は防げません。
- `notify`はComputer Useのturn終了連携に使われるため、Metrics目的でwait通知へ置き換えません。

## Metricsで確認する指標

変更後は`collect.py`と`report.py`で、変更前後を比較します。

- `wait_tool_calls`
- `wait_status_tokens`
- `status_only_turn`
- Root taskの`total_tokens`
- first-pass completion rate
- task duration
- child完了までの再試行回数

期待する変化は次です。

```text
wait/status call数       ↓
status-only token        ↓
同一taskの不要な再評価   ↓
完遂率                   ↔ または ↑
異常時の検知遅延         許容範囲内
```

wait tokenが減っても完遂率が低下したり、異常検知が遅くなった場合は成功とはみなしません。

## 変更・rollback条件

次のいずれかが確認された場合は、20分/25分設定を再検討します。

- status-only tokenが減らない
- child完了の検知遅延が実用上問題になる
- timeout到達後の再試行が増える
- task durationだけが増え、完遂率が改善しない
- Controllerがwait中にユーザー入力へ応答できない

rollback時は、既定値または計測した適切な値へ戻します。変更前後の値、対象task、Metrics結果、rollback理由をCHANGELOGまたは設計判断文書へ残します。

## 関連実装

- [`config.example.toml`](../config.example.toml): 実際のwait timeout設定
- [`metrics/rollout_reader.py`](../metrics/rollout_reader.py): wait/status tool callの抽出
- [`metrics/report.py`](../metrics/report.py): wait/status tokenの集計
