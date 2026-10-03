# Design Decisions

## 2026-10-03: allStandard baseline

Rootと全active roleを `service_tier = "default"` に統一します。model・effort・872K windowは維持し、archived Terraは再登録しません。`fast_mode = true` は選択機能を残すだけでFast要求ではありません。設定の静的検証は実際のrequest tierの証明ではなく、速度・利用枠への効果も未実測です。

以下の2026-10-01節は変更前の判断記録です。Fast設定はこのallStandard方針で置き換えます。

## 2026-10-01: 薄いLuna Routerと6.1 Sol主担当

変更前はLuna Medium RootからScout、Luna Worker、Sol Medium Worker、Sol Controllerへタスクを分類していました。変更後はRootをGPT-6 Luna / medium / Fastに維持し、会話内だけで確実に完結する単純処理以外をGPT-6.1 Sol / high / Standard Controllerへ渡します。

Directにファイルの読取り・編集・調査・検証を含めません。完全指定されたファイルの誤字修正もSolへ渡します。Rootは振り分け前に調査せず、専門的な再分析もせず、主担当のUSER_RESULTを配送します。

## Controllerを通常受付にする

Controllerは調査・判断・実装・検証・最終回答まで通常は自身で完遂します。Workerを起動してから同じSolのControllerへ渡し直すhopを減らします。Leaf-firstを必須にせず、独立性・明確な所有範囲・待ち時間削減の利益がある場合だけScout、Worker、Expertを起動します。

WorkerはControllerが切り出した独立実装、Scoutはread-only事実収集、Expertは局所的な難しい分析に使います。Rootからの直接起動は明示role指定に限ります。Astraは重要な不確実性が残る高失敗コスト問題の例外です。

## Luna系をFastにする

RootとScoutはGPT-6 Luna / medium / Fast、Luna Workerはhigh / Fast、Sol ControllerとWorkerはGPT-6.1 Sol / high / Standard、Expertは6.1 Sol / xhigh / Standard、Astraはhigh / Standardです。すべてのroleにservice_tierを明示して意図しない継承を防ぎます。

Fastの追加クレジット・API料金は同モデルStandardの2倍、プラン内利用枠は2.5倍です。これは速度向上率ではありません。2026-10-01確認の[公式速度説明](https://learn.chatgpt.com/docs/agent-configuration/speed)と[6.1 Sol API料金](https://developers.openai.com/api/docs/models/gpt-6.1-sol)に基づきます。Luna系のFastが全体時間をどれだけ改善するかは未実測です。

## 評価とrollback条件

| 観点 | 期待・評価方法 | rollbackまたは調整条件 |
|---|---|---|
| token / cost | 親子・再試行を合算し、APIと追加クレジットを別推計する | Solへ渡す増分が手戻り・品質改善に見合わない場合、実測した範囲だけLeaf利用を再検討 |
| 完遂率 | 検証PASSと受入条件達成を確認する | 未完遂や誤ったDirect処理が増えたらrouting指示を修正 |
| high effort | 6.1 Sol mediumとの同条件比較 | highの改善が総消費・時間に見合わない課題だけmediumを検討 |
| Fast | 実効tierを観測し、Standardとの総時間・利用枠を比較 | 利益が小さい場合はLuna系のFastを個別に戻す |
| context | 既存872K上限を維持し、実入力・compactを観測 | 常時長文脈による消費増を確認した場合だけ上限を再検討 |

追加クレジット単価からプラン内利用枠を換算しません。effortやmodel単価からタスク総費用の比率を断定しません。並行利用やリセットの影響を除けない利用枠観測は不明とします。

## 検証範囲

TOML解析、installerの既存設定保持、合成rolloutによるMetrics単体テストは静的・ローカルの検証です。実際のmodel・effort・tierの継承、自然言語routing、品質・費用への効果はCodex再起動後の新規sessionで別途確認します。設定値しかない場合やtier欠落のrolloutで実効速度をPASSにしません。

Leafの再委譲禁止、必要情報だけのpacket、重複作業と成功済み検証の反復抑制、受動Metricsを維持します。wait/statusの設計は[`WAIT-POLLING.md`](WAIT-POLLING.md)、以前の経緯は[`ROUTER-HISTORY.md`](ROUTER-HISTORY.md)です。

## 2026-10-03: bounded routingの段階導入 (phase 3–5)

上の2026-10-01記録は変更前の履歴です。現在のrouting契約はAGENTS.mdを参照します。

- phase 3: 初期3-routeと旧Astra基準を維持し、6軸feature card、evidence/unknowns、ROOT_AUTO/CONTROLLER_LEAF/EXPLICIT、versioned offline fixtures/scorerを追加。
- phase 4: 依頼全体が指定sourceの直接表現された事実の取得だけならScoutを許可。比較・推薦・診断・unknownはSolへ。真に難解な推論へのAstra適用は低consequenceでも可能とする変更を明示。
- phase 5: 完全指定の低risk・可逆・局所変換と、受入条件をcoverする既存決定的checkだけならLuna Workerを許可。1ファイルだけでは成立せず、protected/sensitiveやsemantic designは除外。

初期RouteはDirect / Scout / Luna Worker / Sol Controller / Astra Controller。自動Sol Workerは追加しません。Controller配下Leafや明示指定の能力を狭めません。失敗時は証拠と現在状態を引き継ぎ、権限/network不足をmodel escalationで回避しません。成功したLuna Workerへmandatory Sol再reviewを足しません。

期待効果は不要なController hopの削減ですが、品質・総token・待ち時間・費用の改善は未測定です。synthetic test、既存9件の手動ケース、配線Smokeをsemantic live evalと扱いません。current/new/alwaysSolの独立session比較、実spawn role、完遂証拠、全親子usage/time、downgrade/overroute候補を[評価手順](../metrics/docs/ROUTING-EVAL.md)で確認します。危険なdowngradeや受入不成立が増えた場合は該当bounded入口をSolへ戻し、既存成果を維持して理由を記録します。
