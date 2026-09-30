# Codex Multi-Agent Orchestrator

Codex Multi-Agent V2 の公開可能な設定、agent role、Metricsを管理するリポジトリです。

## 安全な設定分離

公開リポジトリには端末固有の `config.toml` を保存しません。利用時は
`config.example.toml` をテンプレートとして `CODEX_HOME/config.toml` に適用します。

```powershell
.\scripts\install.ps1
```

既存の `config.toml` は日時付きバックアップを作成し、公開テンプレートが管理するキー・sectionだけを更新します。`notify`、MCP、trusted projectなど、管理対象外の既存設定は保持します。

適用前に確認する場合:

```powershell
.\scripts\install.ps1 -WhatIf
```

Root routing指示も`CODEX_HOME/AGENTS.md`へ適用する場合は、既存ファイルを日時付きでバックアップしたうえで明示的に指定します。

```powershell
.\scripts\install.ps1 -InstallRootInstructions -WhatIf
.\scripts\install.ps1 -InstallRootInstructions
```

`-InstallRootInstructions`を省略した場合、既存の`CODEX_HOME/AGENTS.md`は変更しません。

個人パス、MCP接続、通知コマンド、trusted project、session、認証情報は公開設定から除外しています。

## 含まれるもの

- `agents/`: Scout、Worker、Controller、Expertのrole設定
- `metrics/`: rollout解析、smoke test、context/返却結果サイズ観測
- `config.example.toml`: 端末非依存の公開テンプレート

`model_auto_compact_token_limit` は意図的に設定していません。Rootと子agentの実測後に判断します。

## 中心思想

Rootは `gpt-6-luna / medium / Fast` の薄いRouterです。会話内で確実に完結する単純な処理だけをDirectで行い、調査・判断・ファイル操作・実装・検証が必要な通常依頼は `gpt-6.1-sol / high / Standard` のControllerへ渡します。

```text
Luna Medium / Fast Root
  ├─ DIRECT_LUNA       会話内だけで完結する単純処理
  ├─ CONTROLLER_SOL    調査・判断・実装・検証の通常主担当
  │    └─ 必要な場合だけScout / Worker / Expert
  └─ CONTROLLER_ASTRA  例外的な高失敗コストの判断
```

Controllerは通常、自分で完遂します。Workerを必ず挟む構成にはせず、独立した作業を切り出す利益がある場合だけLeafを起動します。Rootは主担当のユーザー向け最終回答を受け取り、専門的な再分析を行いません。Composer・依頼本文の明示model/role指定を優先し、適合するRootがいる場合は同じ役割を重複起動しません。

| 担当 | Model | Effort | 速度 |
|---|---|---|---|
| Root Router | GPT-6 Luna | medium | Fast |
| Scout | GPT-6 Luna | medium | Fast |
| Luna Worker | GPT-6 Luna | high | Fast |
| Sol Controller / Worker | GPT-6.1 Sol | high | Standard |
| Expert | GPT-6.1 Sol | xhigh | Standard |
| Astra Controller | GPT-6 Astra | high | Standard |

Root・Scout・Workerを含むLuna系をFastにし、Sol系とAstraのroleは `service_tier = "default"` を明示します。速度を省略してRootのFastを継承しないようにします。`fast`はリクエストの`priority`に対応します。設定の根拠は[公式Subagentsガイド](https://learn.chatgpt.com/docs/agent-configuration/subagents)と[設定リファレンス](https://learn.chatgpt.com/docs/config-file/config-reference)です。実環境でのtierの適用は新規sessionで別途確認します。

## Routing判定表

| 依頼の状態 | 自動初期Route | 例 |
|---|---|---|
| 会話内だけで確実に完結し、ツール・事実確認・内容判断が不要 | `DIRECT_LUNA` | 単純な翻訳、短縮、形式変換、完全指定された文章修正 |
| 調査・判断・ファイル操作・実装・検証が必要、または必要性が不明 | `CONTROLLER_SOL` | 設定探索、ログ列挙、ファイルの誤字修正、原因分析、実装 |
| 明確な最難関の高失敗コスト問題、またはSolからの必要な昇格 | `CONTROLLER_ASTRA` | Solで重要な不確実性が残る問題 |

Scout/Workerは明示role指定、またはController配下のLeafで使います。通常依頼のRoot初期Routeには使いません。Directの条件は小規模であることやagent起動コストを理由に緩めません。詳細とcanonical sourceは[`AGENTS.md`](AGENTS.md)です。

## Contextと結果の扱い

子agentには原則`fork_turns = "none"`を使い、Rootの長い履歴を複製しません。代わりに次のtask packetを渡します。

```text
Objective
Known facts / evidence
Unknowns
Scope
Allowed
Forbidden
Acceptance criteria
Verification
Authorization
Escalation
```

`AGENTS.md`をrouting・delegation・出力契約のcanonical sourceとします。`agents/*.toml`はrole固有の実行境界と固定route/statusを定義し、判定規則を重複して別解釈しません。将来、下位directoryに別の`AGENTS.md`またはrole契約を追加する場合は、canonical sourceへの参照、優先順位、差分理由を同じ変更で文書化します。

Root contextは全agentの作業履歴ではなく、routing stateとdistilled knowledgeを保持する場所です。ControllerはLeafのraw outputをそのままRootへ転送せず、必要な知識へ圧縮します。

`model_context_window = 872000`はheadroomを持たせる設定です。一方、`model_auto_compact_token_limit`はglobal設定せず、Rootと子agentの実測後に判断します。

Context関連の用語は次の意味です。

| 用語 | 日本語での意味 | 値 |
|---|---|---:|
| `catalog.context_window`（モデルの標準値） | config未指定時のcatalog基準値 | 272000 |
| `catalog.max_context_window`（configで指定できる上限） | modelごとに許可された最大値 | 872000 |
| `model_context_window`（config.tomlの指定値） | この構成が実際に指定するwindow | 872000 |

## Metricsの役割

MetricsのSource of Truthは、モデルの自己申告ではなく次のrollout JSONLです。

```text
%USERPROFILE%\\.codex\\sessions\\**\\rollout-*.jsonl
```

- `smoke.py`: 配線、実効model/effort/V2、context観測
- `collect.py`: rolloutからturn単位Metricsを生成
- `report.py`: 期間集計、完遂率、昇格率、token、API USD換算とCodex追加クレジット換算の参考値を表示
- `timeline.py`: session → turn → agentの時系列表示

Metricsは設定を自動変更しません。変更前後、token cost、完遂率、rollback条件を記録し、人間が判断します。
追加クレジット換算はプラン内利用枠の減少量を表しません。自然言語でのRoute判定とSol Workerのeffort比較は[Routing回帰確認](metrics/docs/ROUTING-EVAL.md)に記載します。

## Smoke Testの基本

静的検証は `python -m unittest discover -s scripts -p "test_*.py"` と `python -m unittest discover -s metrics -p "test_*.py"` で実行します。TOMLとinstallerの検証や合成rolloutのPASSを、実際のモデル・速度・自動routingの成功として扱いません。

設定変更後はCodexを完全終了して再起動し、新規sessionで確認します。

```powershell
$SmokeStart = Get-Date
# ここでCodex上のSmoke Testを実行
python "$env:USERPROFILE\\.codex\\metrics\\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_sol_scout `
  --context `
  --table
```

配線Smoke Testと、agent名を指定しない自動Routing Testは別の検証です。Smoke Testは指定したrole/model/階層が実効化されるかだけを確認し、判断品質を証明しません。自動Routing Testは固定のケース表に対するroute選択と、under-routing/over-routingの評価を確認します。Astraは高コストのため、初回から常用しません。ケースと手順は[`metrics/docs/SMOKE.md`](metrics/docs/SMOKE.md)を参照してください。

### Astraの配線Smoke Test

Astraは高価なため、Astra Controllerが起動することだけを最小コストで確認します。AstraからScout、Worker、Expertをspawnさせません。

Codexで次のpromptを実行します。

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

実行直前に開始時刻を保存します。

```powershell
$SmokeStart = Get-Date
```

Codexでpromptを実行した後、次を実行します。

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since "$($SmokeStart.ToString('o'))" `
  --expect controller_astra `
  --table
```

正常時は概ね次のようになります。

```text
[OK] ROOT | gpt-6-luna / medium / v2
└─ [OK] controller_astra | gpt-6-astra / high / v2

RESULT: PASS
```

このテストの目的はAstraの配線確認だけです。Astraがさらにagentをspawnした場合は、期待していない追加tokenの原因として扱います。

## 改修時のルール

一般的なbest practiceへ機械的に寄せず、次を変更記録に残します。

- 変更前 / 変更後
- 変更理由
- token costへの影響
- 完遂率への影響
- rollback条件
- Smoke Test
- Metricsによる評価方法

詳細な設計理由と意思決定は[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)と[`docs/DESIGN-DECISIONS.md`](docs/DESIGN-DECISIONS.md)に整理しています。Luna Rootの設計と実装が一時的にずれていた経緯は[`docs/ROUTER-HISTORY.md`](docs/ROUTER-HISTORY.md)、wait/status pollingの設定理由は[`docs/WAIT-POLLING.md`](docs/WAIT-POLLING.md)に記録しています。
