# Codex Metrics

Codex の Multi-Agent オーケストレーターを、実際の rollout JSONL から検証・計測する受動解析ツール群です。モデルに token 数を推定させず、次を source of truth として読み取ります。

```text
%USERPROFILE%\.codex\sessions\**\rollout-*.jsonl
```

`config.toml`、session、agent、`notify`、hook は変更しません。Python 標準ライブラリだけで動作し、追加 package は不要です。

## Quick Start

```powershell
# 設定導入・変更後: Codex を再起動し、新規 session で Smoke Test を実行してから確認
$SmokeStart = Get-Date
# ここで Codex 上の Smoke Test を実行
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_sol_scout `
  --context --table

# 通常の収集と30日レポート
python "$env:USERPROFILE\.codex\metrics\collect.py"
python "$env:USERPROFILE\.codex\metrics\report.py"

# 直近30分の tree
python "$env:USERPROFILE\.codex\metrics\timeline.py" --minutes 30 --show-tokens
```

配線確認と、agent 名を指定しない自然言語 Routing の確認は別に実施してください。Astra は高コストのため、通常の初回確認には含めません。

## Tool Map

| ファイル | 役割 |
|---|---|
| `rollout_reader.py` | JSONL 解析の共通ライブラリ。通常は直接実行しません |
| `smoke.py` | Root/Child/Grandchild の実効 model・effort・V2・role 配線を検証 |
| `collect.py` | rollout を turn 単位の `routing-metrics.jsonl` に収集 |
| `report.py` | Routing 品質、token、昇格、待機、返却結果サイズを集計 |
| `timeline.py` | Session → Turn → Agent tree または event 時系列を表示 |
| `cost-weights.json` | 任意の重み付きコスト設定 |
| `state.json` | `collect.py` の rollout 解析キャッシュ |
| `routing-metrics.jsonl` | 収集済み Metrics 本体 |

## 共通概念

- Metrics は観測専用です。設定を自動変更しないため、変更前後・token cost・完遂率・rollback 条件を記録して人間が判断します。
- token は JSONL 中の全値を単純加算せず、live turn の使用量、cumulative snapshot の重複、親子帰属を処理します。
- machine-readable な field 名は互換性のため英語のまま固定です。
- `possible_immediate_rework` は確定値ではなく、Root 完了後30分以内の訂正らしい次発話を検出する heuristic です。

## 詳細

- [Smoke Test と配線検証](docs/SMOKE.md)
- [収集・キャッシュ・token accounting](docs/COLLECT.md)
- [レポートの項目と options](docs/REPORT.md)
- [Timeline の tree・events・live 表示](docs/TIMELINE.md)
- [Metrics JSONL field 定義](docs/METRICS-JSONL.md)
- [運用とトラブルシューティング](docs/OPERATIONS.md)
