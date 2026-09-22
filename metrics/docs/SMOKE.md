# Smoke Test と配線検証

設定導入・変更後は Codex を完全終了して再起動し、新規 session で実施します。画面表示ではなく rollout JSONL の**実効値**を確認します。

```powershell
$SmokeStart = Get-Date
# この後、Codexで下記いずれかのpromptを実行
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --table
```

Windows PowerShell の `.ToString("o")` の小数秒7桁 .NET形式にも対応します。直近だけを見るなら `--minutes 15 --table` を使います。

## Root / Scout / Worker / Sol / Astra の prompt

各 prompt は指定した tree だけを確認するものです。終了後は対応する `--expect` を付けて検証します。

### Root

```text
Multi-Agent Smoke Testです。

Root自身で、このrepositoryのトップレベル構成をread-onlyで簡単に確認してください。
agentは起動しないでください。ファイル変更、実装、詳細調査は不要です。
確認後、ROOT_SMOKE_OKを含む簡潔な結果を返してください。
```

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" --since $SmokeStart.ToString("o") --expect root
```

期待: `ROOT` は `gpt-5.6-luna / medium / v2`。

### Luna Scout

```text
Multi-Agent Smoke Testです。

Rootは scout agent を1体だけ起動してください。Root自身では調査しないでください。
scoutは他のagentを起動せず、read-onlyでこのrepositoryのトップレベル構成を簡単に確認し、SCOUT_SMOKE_OKを含む結果をRootへ返してください。
ファイル変更、実装、詳細調査は不要です。不要なagentは起動しないでください。
```

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" --since $SmokeStart.ToString("o") --expect scout
```

期待:

```text
ROOT
└─ scout
```

### Terra Worker

```text
Multi-Agent Smoke Testです。

Rootは worker_terra agent を1体だけ起動してください。Root自身ではタスクを処理しないでください。
worker_terraは他のagentを起動せず、read-onlyでこのrepositoryのトップレベル構成を簡単に確認し、WORKER_TERRA_SMOKE_OKを含む結果をRootへ返してください。
ファイル変更、実装、詳細調査は不要です。不要なagentは起動しないでください。
```

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" --since $SmokeStart.ToString("o") --expect worker_terra
```

期待:

```text
ROOT                gpt-5.6-luna  medium  v2
└─ worker_terra     gpt-5.6-terra high    v2
```

### Sol Controller

単体確認:

```text
Multi-Agent Smoke Testです。

Rootは controller_sol agent を1体だけ起動してください。Root自身ではタスクを処理しないでください。
controller_solは他のagentを起動せず、read-onlyでこのrepositoryのトップレベル構成を簡単に確認し、CONTROLLER_SOL_SMOKE_OKを含む結果をRootへ返してください。
ファイル変更、実装、詳細調査は不要です。不要なagentは起動しないでください。
```

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" --since $SmokeStart.ToString("o") --expect controller_sol
```

nested delegation（最重要）:

```text
Multi-Agent Smoke Testです。

Rootは controller_sol agent を1体だけ起動してください。Root自身ではタスクを処理しないでください。
controller_solは scout agent を1体だけ起動してください。scoutは他のagentを起動せず、read-onlyでこのrepositoryのトップレベル構成を簡単に確認し、SOL_SCOUT_SMOKE_OKをcontroller_solへ返してください。
controller_solは結果を簡潔にRootへ返してください。ファイル変更、実装、詳細調査は不要です。不要なagentは起動しないでください。
```

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_sol_scout --table
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

### Astra Controller

Astra は高コストなので初期導入時に必須ではありません。起動だけを最小コストで確認し、Scout・Worker・Expert を spawn させません。

```text
Multi-Agent Smoke Testです。

Rootは必ず controller_astra agent を1体だけ起動してください。
Root自身ではタスクを処理しないでください。

controller_astra は他のagentを起動せず、read-onlyでこのrepositoryのトップレベル構成を簡単に確認してください。
ファイルの変更、実装、詳細調査は不要です。

確認が完了したら、controller_astra は次の文字列を含めてRootへ結果を返してください。

ASTRA_SMOKE_OK

Rootはcontroller_astraの結果をそのまま簡潔にまとめてください。

不要なagentは起動しないでください。
```

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_astra --table
```

期待:

```text
[OK] ROOT | gpt-5.6-luna / medium / v2
└─ [OK] controller_astra | gpt-6-astra / high / v2

RESULT: PASS
```

追加 agent は期待していない token の原因として扱います。

## 配線Smoke Testと自動Routing Testを混同しない

上のSmoke Testは、promptで**指定した**role、model、effort、treeが実効化されるかを`smoke.py --expect`で確認する配線検証である。Rootが依頼内容から適切なrouteを選べること、またはtoken効率を証明するテストではない。

自動Routing Testは、agent名を指定せず、固定した自然言語ケースを新規sessionで一件ずつ実行する運用評価である。開始時刻をケースごとに記録し、`collect.py`と`report.py`で実際の`initial_route`、完了、初回完遂、手戻り、token、subagent数を確認する。現在のMetricsは受動解析であり、期待routeとの突合や設定の自動変更は行わない。

| ケース | promptの要旨 | 期待する最小route | 観測上の失敗 |
|---|---|---|---|
| Direct | 「次の文章を短くして」またはpath・値・確認項目が完全指定された単一設定の確認 | `DIRECT_LUNA` | 未知の場所を探索した場合はDirect成立条件違反。Worker/Controller起動はover-routing。 |
| Scout | 「変更せず、このsymbolの呼出経路と関連testを調べて」 | `SCOUT_LUNA` | 書込み・実装はoverreach。Directで根拠不足の結論はunder-routing。 |
| Worker | 「この明白なbugを指定fileで修正し、対象testを通して」 | `WORKER_TERRA` | Directが設計判断や広い調査を抱えるのはunder-routing。Sol/Astraは根拠なきover-routing。 |
| Sol | 「複数moduleで原因不明の回帰を診断し、選択肢を統合して修正して」 | `CONTROLLER_SOL` | Workerが未解消の複数module不確実性を推測で閉じるのはunder-routing。Astraは高risk根拠なしならover-routing。 |
| Astra | 「security-sensitiveな破壊的migrationの設計判断を、失敗影響付きで検討して」 | `CONTROLLER_ASTRA` | Sol以下で重大riskを推測で処理するのはunder-routing。実際に低riskならAstraはover-routing。 |
| 明示Terra | ComposerでTerraを選択するか「Terraで実装して」と指定 | `WORKER_TERRA` | Luna自動判定へ戻す、または同tier Workerを重複起動する。 |
| 明示Sol | ComposerでSolを選択するか「Solで調査して」と指定 | `CONTROLLER_SOL` | Scout/Workerへ降格する、または同tier Controllerを重複起動する。 |
| 明示Astra | ComposerでAstraを選択するか「Astraで判断して」と指定 | `CONTROLLER_ASTRA` | 自動判定で下位Routeへ変更する、または同tier Controllerを重複起動する。 |

### 実施例

```powershell
$RoutingStart = Get-Date
# Codexで、role名を含めない上表のケースを1件だけ実行する
python "$env:USERPROFILE\.codex\metrics\collect.py"
python "$env:USERPROFILE\.codex\metrics\report.py" `
  --since $RoutingStart.ToString("o")
```

期待routeとの一致だけで合否を決めない。under-routingは、低tierで`FAIL`・再試行・昇格・モデル訂正らしい手戻りが増えること、または必要な検証が欠けることで確認する。over-routingは、より高tier/追加agentを使っても完了率・初回完遂率が改善せず、task token・待機・subagent数だけが増えることで確認する。比較では同種のケースを複数回実施し、同じ期間境界で前後のMetricsを読む。

## context と内部 session

`--context` は `token_count.info.model_context_window` と `last_token_usage.total_tokens` から、実効window、最後の使用量、session内peak、各使用率を表示します。累積 `total_token_usage` はコスト計測用で context 使用率には使いません。欠損は model/effort/配線の PASS/FAIL を変えない非致命の警告です。`--json` の session 項目には `context_window`、`context_tokens`、`context_peak_tokens`、`context_usage_pct`、`context_peak_usage_pct`（不明は `null`）を常に含めます。

Guardian / Review / Compact / Auto Review 等の内部補助 session（例: `codex-auto-review / low / disabled`）は通常 Root ではありません。通常 Root と ThreadSpawn された `scout`、`worker_terra`、`controller_sol`、`expert`、`controller_astra` だけを検証対象にし、内部 session は `--table` で参考表示するだけです。

## options と結果

| option | 内容 | 既定値 |
|---|---|---|
| `--codex-home PATH` | Codex home | `CODEX_HOME` または `~/.codex` |
| `--sessions-root PATH` | sessions を直接指定 | `<CODEX_HOME>/sessions` |
| `--since DATETIME` | 指定時刻以降の rollout | 未指定 |
| `--minutes N` | `--since` 未指定時の直近分数 | `10` |
| `--table` | session 詳細表 | OFF |
| `--context` | context window と使用率 | OFF |
| `--json` | JSON 出力 | OFF |
| `--expect NAME` | 期待 tree を厳密検証 | 未指定 |

`--expect`: `root`、`scout`、`worker_terra`、`controller_sol`、`controller_sol_scout`、`controller_astra`。

| exit code | 意味 |
|---:|---|
| `0` | PASS |
| `1` | rollout は読めたが設定・配線に警告あり |
| `2` | sessions/rollout がないなど、テスト自体を実行できない |

`turn_context` がない警告は model / effort / V1/V2 を実ログから確認できない状態であり、PASS にはなりません。
