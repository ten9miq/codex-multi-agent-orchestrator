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
| `smoke.py` | Root/Child/Grandchild のturn設定観測・V2・role配線を検証。request tierは別の証拠 |
| `collect.py` | rollout を turn 単位の `routing-metrics.jsonl` に収集 |
| `report.py` | Routing 品質、token、昇格、待機、返却結果サイズを集計 |
| `timeline.py` | Session → Turn → Agent tree または event 時系列を表示 |
| `api_cost.py` | 根拠付きrequest APIと現行turnの未分類coverage。実rollout adapterは未接続 |
| `api-pricing-2026-10-03.json` | model別の検証済み短/長文脈API参考単価snapshot |
| `cost-weights.json` | API Standard短文脈単価によるUSD換算の参考値 |
| `codex-credit-rates.json` | Codex Standard速度を基準にした追加クレジット単価の参考値 |
| `state.json` | `collect.py` の rollout 解析キャッシュ |
| `routing-metrics.jsonl` | 収集済み Metrics 本体 |

## 共通概念

- Metrics は観測専用です。設定を自動変更しないため、変更前後・token cost・完遂率・rollback 条件を記録して人間が判断します。
- token は JSONL 中の全値を単純加算せず、live turn の使用量、cumulative snapshot の重複、親子帰属を処理します。
- machine-readable な field 名は互換性のため英語のまま固定です。
- `first_spawn_role` はRoot turnで最初に観測した既知のnamed spawn roleです。選択された`initial_route`とは独立に集計し、Routeの推定には使いません。
- API USD換算とCodex追加クレジット換算は別の推計です。どちらもCodexのプラン内利用枠の減少量や請求額を表しません。料金表の日付・出典・速度条件は各JSONに記録します。
- `turn_context.service_tier` は `observed_turn` の証拠です。`priority`→`fast`、`default`→`standard`に正規化しますが、実requestへの適用を証明しません。全観測の出所・順序・値を保存し、turn内で変更・未知値・欠損があればsummaryは`UNKNOWN`です。次turnへtierを持ち越しません。
- `configured_service_tier`、`observed_service_tier`、`requested_service_tier`を分離します。現行readerは設定ファイルもrequest traceも読まないため、configured/requestedは`UNKNOWN`、request適用は`UNVERIFIED`です。旧cacheは元rolloutから再解析し、現在のconfigで過去を補完しません。
- Smokeは全active roleで`standard`を期待し、成功は`RESULT: PASS (TURN_CONTEXT_PROFILE_ONLY)`と表示します。これは観測されたturn設定と配線に限るPASSで、`REQUEST_TIER: UNVERIFIED`は別に残ります。
- 料金の既定動作は未算定です。比較用には`report.py --scenario-tier standard`（または`fast`）を明示すると、全tokenへ速度を仮定した短文脈基準値を出せます。Fastシナリオは基準単価の2倍ですが、cache write・長文脈などのrequest条件は再現しません。API USD、Codex追加クレジット、Pro等のプラン内利用枠は別概念で、プラン内利用枠消費は推定できません。
- `rework_class` は連続Root turnの次発話を `NONE` / `USER_FOLLOWUP` / `MODEL_CORRECTION` / `UNKNOWN` に控えめに分類します。`possible_immediate_rework` は互換用の旧heuristicです。

## 詳細

- [Smoke Test と配線検証](docs/SMOKE.md)
- [収集・キャッシュ・token accounting](docs/COLLECT.md)
- [レポートの項目と options](docs/REPORT.md)
- [根拠付きAPI token料金・未分類coverage](docs/API-COST.md)
- [Timeline の tree・events・live 表示](docs/TIMELINE.md)
- [Metrics JSONL field 定義](docs/METRICS-JSONL.md)
- [運用とトラブルシューティング](docs/OPERATIONS.md)
- [Routing回帰確認とSol effort比較](docs/ROUTING-EVAL.md)
