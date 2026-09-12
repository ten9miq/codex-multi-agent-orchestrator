# Codex Metrics

CodexのMulti-Agentオーケストレーターを、**実際のrollout JSONL**から検証・計測するためのツール群です。

モデル自身にtoken数を推定させず、次をsource of truthとして読み取ります。

```text
%USERPROFILE%\.codex\sessions\**\rollout-*.jsonl
```

このMetrics機能は**受動解析のみ**です。`config.toml`、session、agent、`notify`、hookなどを変更しません。

---

## ファイル構成

```text
metrics\
├── README.md
├── rollout_reader.py
├── collect.py
├── report.py
├── timeline.py
├── smoke.py
├── cost-weights.json
├── state.json
└── routing-metrics.jsonl
```

| ファイル | 役割 |
|---|---|
| `rollout_reader.py` | JSONL解析の共通ライブラリ。通常は直接実行しません |
| `collect.py` | 全rolloutを解析して `routing-metrics.jsonl` を生成 |
| `report.py` | Metricsを集計して日本語レポートを表示 |
| `timeline.py` | Session → Turn → Agentのtree、またはsession横断event時系列を表示 |
| `smoke.py` | Root/Child/Grandchildの実効model・effort・V2・roleを検証 |
| `cost-weights.json` | 任意の重み付きコスト計算設定 |
| `state.json` | `collect.py` の解析キャッシュ状態 |
| `routing-metrics.jsonl` | 収集済みMetrics本体 |

Python標準ライブラリだけで動作します。追加packageは不要です。

---

# 1. 最初に行うSmoke Test

設定導入直後は、画面表示だけでなくrollout JSONLに記録された**実効値**を確認します。

## 1-1. Smoke Test開始時刻を保存

PowerShell:

```powershell
$SmokeStart = Get-Date
```

その後CodexでSmoke Testを実行します。

## 1-2. 実効配線を確認

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --table
```

Windows PowerShellの `.ToString("o")` が出す小数秒7桁の.NET形式にも対応しています。

### 直近15分を見るだけなら

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" --minutes 15 --table
```

### Context windowも確認する

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --table --context
```

`--context` はrolloutの `token_count.info.model_context_window` と
`last_token_usage.total_tokens` から、実効window、最後の使用量、session内peak、各使用率を表示します。
累積の `total_token_usage` はコスト計測用であり、context使用率には使いません。古いrolloutなどで
これらの値が欠損しても、既存のmodel/effort/配線のPASS/FAILには影響しない非致命の警告になります。
`--json` のsession項目には常に `context_window`、`context_tokens`、`context_peak_tokens`、
`context_usage_pct`、`context_peak_usage_pct`（不明なら `null`）を含めます。

---

# 2. Smoke Testの期待配線を厳密に確認する

`--expect` を指定すると、model/effort/V2だけでなく**agent treeの形**も判定します。

## Rootのみ

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect root
```

期待:

```text
ROOT
gpt-5.6-luna / medium / v2
```

## Luna Scout

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect scout
```

期待:

```text
ROOT
└─ scout
```

## Terra Worker

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect worker_terra
```

期待:

```text
ROOT                gpt-5.6-luna  medium  v2
└─ worker_terra     gpt-5.6-terra high    v2
```

## Sol Controller

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_sol
```

期待:

```text
ROOT
└─ controller_sol
```

## Sol Controller → Luna Scout

今回のnested delegationを確認する最重要テストです。

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_sol_scout `
  --table
```

正常例:

```text
Codex Multi-Agent Smoke Test
対象開始時刻 : 2026-09-12T20:31:28+09:00
rollout数    : 3
期待配線      : controller_sol_scout

[OK] ROOT | gpt-5.6-luna / medium / v2
└─ [OK] controller_sol | gpt-5.6-sol / medium / v2
   └─ [OK] scout | gpt-5.6-luna / medium / v2

RESULT: PASS
実効model / effort / Multi-Agent runtime / role配線は期待値と一致しています。
```

## Astra Controller

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_astra
```

Astraは高コストなので、初期導入時に必ず実行する必要はありません。

---


## Guardian / Auto Reviewの内部sessionについて

Codexは通常タスクと同じ時間帯に、次のような内部補助sessionを生成する場合があります。

```text
codex-auto-review / low / disabled
```

これは今回のMulti-Agent OrchestratorのRootではなく、Guardian / Auto Review等の内部処理です。

`smoke.py` は以下だけを配線検証対象にします。

```text
通常Root
ThreadSpawnされた scout / worker_terra / controller_sol / expert / controller_astra
```

Guardian / Review / Compact / Auto Review等の内部補助sessionはSmoke Test判定から除外し、
`--table` 使用時には「Smoke Test対象外の内部補助session」として参考表示します。

そのため、内部Auto Reviewが同時に動いていても `--expect scout` 等のPASS/FAILには影響しません。


# 3. `smoke.py` のオプション

| オプション | 内容 | 既定値 |
|---|---|---|
| `--codex-home PATH` | Codexホーム | `CODEX_HOME` または `~/.codex` |
| `--sessions-root PATH` | sessionsディレクトリを直接指定 | `<CODEX_HOME>/sessions` |
| `--since DATETIME` | 指定時刻以降のrolloutだけを見る | 未指定 |
| `--minutes N` | `--since`未指定時、直近N分を見る | `10` |
| `--table` | session詳細表も表示 | OFF |
| `--context` | context window・現在/peak使用量・使用率を表示 | OFF |
| `--json` | JSON形式で結果を出力 | OFF |
| `--expect NAME` | 期待するtree形状を厳密検証 | 未指定 |

`--expect` の値:

```text
root
scout
worker_terra
controller_sol
controller_sol_scout
controller_astra
```

## Exit code

| code | 意味 |
|---:|---|
| `0` | PASS |
| `1` | rolloutは読めたが設定/配線に警告あり |
| `2` | sessions/rolloutが見つからない等、テスト自体を実行できない |

---

# 4. Metricsを収集する

通常利用では、まず次を実行します。

```powershell
python "$env:USERPROFILE\.codex\metrics\collect.py"
```

正常例:

```text
Codex Metrics収集完了
  rolloutファイル数 : 125
  turnレコード数     : 418
  出力先             : C:\Users\...\ .codex\metrics\routing-metrics.jsonl
  状態ファイル       : C:\Users\...\ .codex\metrics\state.json
```

`collect.py` は同じrolloutが変更されていなければ `state.json` のキャッシュを利用します。

## `collect.py` のオプション

| オプション | 内容 |
|---|---|
| `--codex-home PATH` | Codexホームを変更 |
| `--output PATH` | `routing-metrics.jsonl` の出力先を変更 |

例:

```powershell
python "$env:USERPROFILE\.codex\metrics\collect.py" `
  --output "D:\tmp\routing-metrics.jsonl"
```

---

# 5. レポートを見る

収集後:

```powershell
python "$env:USERPROFILE\.codex\metrics\report.py"
```

直近7日だけ:

```powershell
python "$env:USERPROFILE\.codex\metrics\report.py" --days 7
```

主に次を表示します。

- Rootタスク数
- 初期Routeの分布
- 完了率
- First-pass completion rate
- 即時手戻り候補率
- verification failure rate
- Terra → Sol昇格率
- Sol → Astra昇格率
- モデル別token
- task単位 average / median / P90 token
- cached input比率
- wait/status tool call
- status-only token
- 任意のweighted cost

## `report.py` のオプション

| オプション | 内容 | 既定値 |
|---|---|---|
| `--input PATH` | Metrics JSONL | `~/.codex/metrics/routing-metrics.jsonl` |
| `--days N` | 直近N日を集計 | `30` |
| `--weights PATH` | cost weight設定 | `~/.codex/metrics/cost-weights.json` |

---

# 6. Timelineを見る

`timeline.py` は集計済みMetricsを読み取り専用で表示します。既定では
`root_thread_id` → `root_turn_id` → `parent_thread_id` / `thread_id` のtreeです。

```powershell
python "$env:USERPROFILE\.codex\metrics\timeline.py" --minutes 30 --show-tokens
```

sessionをまたいだ開始時刻順のevent表示は次です。

```powershell
python "$env:USERPROFILE\.codex\metrics\timeline.py" --view events --minutes 30
```

収集済みの `routing-metrics.jsonl` を待たず、現在書き込み中を含むrolloutを直接
解析するには `--live` を指定します。`collect.py` のキャッシュや出力ファイルは変更せず、
`sessions\**\rollout-*.jsonl` を読んで同じSession → Turn → Agent treeを構築します。
`--minutes` / `--since` / `--session` を指定した場合は、最初に全rolloutからsession ID、
parent関係、turnの開始・完了時刻だけを軽量に読み、対象rolloutとその祖先だけを詳細解析します。
これによりtoken/tool本文の解析対象を減らしつつ、表示期間より前に始まったRoot turnへの
childの帰属を維持します。親IDが一意でない場合はsessionをまたいで推測せず、未帰属として
隔離します。永続インデックスやtimeline専用cacheは作成しないため、毎回軽量passは行います。
短い期間・単一sessionほど効果が大きく、全期間を無指定で表示する場合は全rolloutが詳細解析
対象になるため、従来と同程度です。書き込み中JSONLの不完全な末尾行は無視されます。

```powershell
# 直近30分のlive tree
python "$env:USERPROFILE\.codex\metrics\timeline.py" --live --minutes 30 --show-tokens

# live rolloutを開始時刻順のJSONで取得
python "$env:USERPROFILE\.codex\metrics\timeline.py" --live --view events --minutes 30 --json
```

`--live` と `--input` は同時に指定できません。前者はrollout直接読込、後者は指定した
Metrics JSONL読込のため、混在を避けて明確にエラーにします。

主なオプションは `--session`（`--root-thread` と同義）、`--since`、`--input`、
`--live`、`--json`、`--show-tokens` です。`root_turn_id` がないchildなど、帰属が曖昧な行は
`(unassigned)` と表示し、別sessionと親子化しません。
live JSONの `rollout_files` は軽量passの全ファイル数、`parsed_rollout_files` は詳細解析した
対象ファイル数（帰属計算用の祖先を含む）です。
人間向け表示のIDは12文字を超える場合に末尾8文字へ短縮しますが、`--json` のIDは完全な値を維持します。
人間向けの時刻は実行環境のローカル時刻で表示します（日本環境ではJST、`+09:00`）。
保存されるMetrics JSONLと`--json`出力の `timestamp` / `end_timestamp` 等はUTCの元の値を維持します。

---

# 7. `routing-metrics.jsonl` の主な項目

machine-readableなfield名は将来の集計・互換性のため**英語のまま固定**します。

| field | 内容 |
|---|---|
| `timestamp` | turn開始時刻 |
| `end_timestamp` | turn終了時刻 |
| `thread_id` | session/thread ID |
| `parent_thread_id` | 親thread ID |
| `root_thread_id` | Root thread |
| `root_turn_id` | 帰属するRoot turn |
| `turn_id` | turn ID |
| `is_root` | Rootか |
| `agent_role` | `scout` / `worker_terra` 等 |
| `route` | 最終Route |
| `initial_route` | 最初に選択したRoute |
| `final_route` | 最終Route |
| `model` | 実効model |
| `reasoning_effort` | 実効reasoning effort |
| `multi_agent_version` | 実効V1/V2 |
| `input_tokens` | input token |
| `cached_input_tokens` | cached input token |
| `output_tokens` | output token |
| `reasoning_tokens` | reasoning token |
| `total_tokens` | total token |
| `status` | `COMPLETE` 等 |
| `verification` | `PASS` / `FAIL` / `NOT_RUN` 等 |
| `escalation_count` | 昇格回数 |
| `retry_count` | retry回数 |
| `subagent_count` | spawnしたsubagent数 |
| `duration_seconds` | turn時間 |
| `wait_tool_calls` | wait/status系call数 |
| `status_only_turn` | 待機/状態確認だけのturnか |
| `wait_status_tokens` | status-only turnのtoken |
| `possible_immediate_rework` | 即時手戻り候補 |
| `first_pass_success` | 初回完遂判定 |
| `result_chars` | assistant返却結果の文字数 |
| `user_result_chars` | `USER_RESULT_BEGIN/END` 内の文字数 |
| `result_estimated_tokens` | assistant結果の概算token（文字数÷4） |
| `user_result_estimated_tokens` | USER_RESULTの概算token（文字数÷4） |

---

# 8. token集計について

Codexのsubagent rolloutでは、fork時に親の過去履歴やtoken記録が含まれる場合があります。

このMetricsでは単純に全 `token_count` を足しません。

主に次を行います。

- live turn開始後のusageを対象化
- cumulative token snapshotの重複を除外
- `last_token_usage` をturn単位で加算
- Root / Child / Grandchildを `parent_thread_id` から関連付け
- Child usageを該当Root taskへ帰属

そのため、一般的な「JSONL中のtoken値を全部sumする」方式よりfork treeでの過大集計を避ける設計です。

---

# 9. 即時手戻りについて

`possible_immediate_rework` は**確定値ではありません**。

Root完了後30分以内の次turnに、

```text
違う
修正
やり直し
ではなく
wrong
fix it
still
again
```

等の訂正らしい表現がある場合に候補として記録します。

新しい追加依頼との完全な区別はできないため、レポートでも「即時手戻り候補」として表示します。

---

# 10. 重み付きコスト

初期状態の `cost-weights.json` は全modelが `null` なので無効です。

例:

```json
{
  "models": {
    "gpt-5.6-luna": {
      "input": 1.0,
      "cached_input": 0.1,
      "output": 6.0,
      "reasoning": 6.0
    },
    "gpt-5.6-terra": {
      "input": 2.5,
      "cached_input": 0.25,
      "output": 15.0,
      "reasoning": 15.0
    }
  }
}
```

値は「100万tokenあたりの任意weight」です。

重要:

> `weighted cost` はAPI料金またはCodex subscription creditそのものとは限りません。

raw tokenは常に保存しているため、weightを後から変更して過去Metricsを再計算できます。

## 10-1. Agent返却結果サイズ（soft compaction観測）

`rollout_reader.py` はassistant返却本文と、返却契約の
`USER_RESULT_BEGIN` から `USER_RESULT_END` までを文字数で計測します。
`result_estimated_tokens` と `user_result_estimated_tokens` は、tokenizerに依存しない
比較用の概算値（文字数÷4）です。既存rolloutや契約外の返却は0として扱うため、
この値だけで過去の返却が圧縮済みだったとは判定しません。

`report.py` はassistant結果とUSER_RESULTについて、平均・中央値・P90・最大を表示します。
これは設定を自動変更する機能ではなく、Rootへ戻す結果サイズを調整するための観測値です。

---

# 11. 推奨運用

## 設定変更直後

```text
Smoke Test
↓
smoke.py
↓
raw rolloutと実効設定を確認
```

## 通常

```text
collect.py
↓
report.py
↓
人間がRouting品質/コストを確認
↓
必要に応じてAGENTS.mdやagent設定を調整
```

初期版ではMetricsから設定を自動変更しません。

---

# 12. トラブルシューティング

## rolloutが見つからない

```text
...以降のrolloutが見つかりません
```

対象時間を広げます。

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" --minutes 30 --table
```

## Codexが書き込み中

対応済みです。

Windowsでは共有読み取りでJSONLを開くため、Codexがsessionを保持中でも読み取れます。書き込み途中の最終JSON行は無視し、次回実行時に再取得します。

## `turn_context` がない

`smoke.py` が警告します。

```text
! turn_context が見つかりません
```

これはmodel / effort / V1/V2を実ログから確認できなかった状態なので、PASSにはしません。

## Smoke Testが複数Rootを拾う

`--expect` は選択期間内にRootが1つであることを前提にしています。

Smoke Test直前に、

```powershell
$SmokeStart = Get-Date
```

を記録し、`--since` を使うのが最も確実です。

---

# 13. よく使うコマンド

```powershell
# 直近10分の配線を見る
python "$env:USERPROFILE\.codex\metrics\smoke.py"

# Smoke Test開始時刻以降を厳密検証
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_sol_scout `
  --table

# Metrics収集
python "$env:USERPROFILE\.codex\metrics\collect.py"

# 30日レポート
python "$env:USERPROFILE\.codex\metrics\report.py"

# 7日レポート
python "$env:USERPROFILE\.codex\metrics\report.py" --days 7
```
