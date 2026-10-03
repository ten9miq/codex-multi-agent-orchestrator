# Routing回帰確認とSol effort比較

## 既存の9件: 手動ケース表 (実測結果ではない)

Smoke Testはmodel/roleの配線確認であり、下表の意味判断を自動判定しない。安全な使い捨てrepositoryと新規sessionで実行し、各turnについて「期待Route・実際に選んだRoute・spawn role・結果の検証・根拠」を記録する。RootのRouteはモデル名やtool未使用だけから確定しない。曖昧なケースを自動的に成功扱いしない。

| ID | 依頼の形 | 期待Route | 確認点 |
|---|---|---|---|
| D1 | 本文を全て貼り、「この文章を短くして」 | `DIRECT_LUNA` | 外部資料の探索が不要 |
| D2 | コード本文を全て貼り、「このコードの意味を説明して」 | `CONTROLLER_SOL` | コードの内容解釈が必要 |
| S1 | 「このrepositoryでfoo関数の呼び出し元を調べて」 | `CONTROLLER_SOL` | symbol探索が必要 |
| S2 | 「現在の実装と過去commitを比較して」 | `CONTROLLER_SOL` | 履歴との照合が必要 |
| S3 | 「設定値がどこで定義されているか調べて」 | `CONTROLLER_SOL` | 設定元が不明 |
| W1 | path・該当行・変更前後の文字列を完全指定したtypo修正 | `CONTROLLER_SOL` | 実ファイルの編集・確認が必要 |
| W2 | 既知の1ファイルで再現条件と期待挙動が明確なbug修正 | `CONTROLLER_SOL` | 挙動変更と検証が必要 |
| W3 | 既知の複数ファイルにまたがる難しいが境界明確な実装 | `CONTROLLER_SOL` | 実装と検証が必要 |
| C1 | 原因不明の複数moduleの不具合を調べ、修正方針を決める | `CONTROLLER_SOL` | 調査結果の統合と設計判断が必要 |

Rootの自動初期Routeは、会話内の情報だけで完結するD1を除き`CONTROLLER_SOL`とする。`SCOUT_LUNA` / `WORKER_LUNA` / `WORKER_SOL`は明示role指定か、Controllerが独立したLeaf作業として切り出したときだけ使う。実際の動作を伴うW1～W3は使い捨てrepositoryで行い、差分と必要なテストを確認する。Astraのケースは通常の回帰実行から外し、必要時だけ別途確認する。

## `worker_sol` High / Medium 比較

`worker_sol`の設定はGPT-6.1 Sol / High / Standard。Mediumとの比較を行う場合は、同じ受入条件を持つ複数の実装課題を選び、別session・独立checkoutで実施する。effortだけを切り替え、モデル、課題、環境、速度設定は合わせる。

各課題で検証PASS、受入条件の達成、人手で確認した手戻り、Rootとchildを合計したtoken、API USD換算、Codex追加クレジット換算、所要時間を記録する。`first_pass_success`はrollout由来の推定値であり、成果物の合否の代わりにしない。失敗後の再試行や昇格は同じ課題の総量に含める。追加クレジット換算からプラン内利用枠の減少量を推定しない。

HighとMediumの品質・総費用への効果は実測済みと扱わない。比較結果を確認してから設定変更の要否を判断する。

## Phase 3: versioned offline evaluator

6軸の固定feature cardは[AGENTS.md](../../AGENTS.md)をcanonical sourceとする。`metrics/fixtures/routing/v1.json` はschema version 1、fixture version `bounded-routing-v1`。各ケースにはprompt、ROOT_AUTO mode、各軸のvalue/evidence、unknowns、policy別のacceptable route set、検証の要否、positive/negative対を保存する。単一の正解を無理に固定せず、妥当な分岐は複数routeを許容できる。変更時はケースと理由をreviewする。

```shell
python -m unittest discover -s metrics -p 'test_routing_eval.py'
python metrics/routing_eval.py
python metrics/routing_eval.py --observations /path/to/reviewed-observations.json
```

最初の2つはoffline検査だけ。evaluatorはネットワーク接続、モデル実行、paid run、session探索、設定変更を行わない。`expected_route` はpolicy expectationの参照関数で、runtime dispatcherではない。引数なしのJSONは `policy_expectations_only` を明示し、実測値を作らない。通常終了code 0は入力を評価できたことだけで、全ケース合格ではない。入力不整合は2。

### 観測レコード

入力はJSON objectで `schema_version: 1`、一致する `fixture_version`、`data_kind: synthetic | recorded_live`、`runs` を持つ。syntheticとrecorded_liveは同じfileで混ぜず、集計も比較も分離する。recorded_liveも自動的に品質を証明せず、証拠の独立reviewが必要。

各runには以下を記録する。

- `run_id`、`case_id`、`policy` (`current / phase3 / always_sol`)
- 実際の `actual_initial_route`、`route_evidence` (Rootの決定が確認できる記録参照)
- 実際の `actual_spawned_role` と `spawn_evidence` (spawn記録参照)。Directはroleを明示的にnullとし、未spawnを確認できる記録を付ける。同じmodelのWorker/Controllerをmodel名から推定しない。
- `root_tool_calls`、`root_file_access`。Directは0/falseでなければ契約違反。
- `completion`: `status`、`acceptance_met`、具体的な `evidence`、`verification: PASS | FAIL | NOT_RUN`、`verification_evidence`。COMPLETE自己申告だけでは成功にしない。受入未達、FAIL、必要な検証NOT_RUN、証拠欠落は未証明。
- `agents`: Rootと全child/descendantの `session_id`、`parent_session_id` (Rootはnull)、`input_tokens`、`cached_input_tokens` (inputの内数)、`output_tokens`、`elapsed_seconds`、rollout参照の `evidence`。modelの自己申告tokenは使わない。
- `agent_tree_complete`: descendantも含む全体を確認できたか。欠損・未確認値はnullとし、観測subtotalは別表示。欠損を0として合計しない。
- `task_wall_seconds` と `timing_evidence`: task開始から完了まで。agent秒の和は並列時のwall timeと別に表示する。

記録されたrouteがacceptable set内かと、完遂の証拠を別々に採点する。spawn roleとの矛盾やDirectのtool使用はcontract_violation。低いrouteへの逸脱をdangerous_downgrade、高いrouteへの逸脱をoverroute_candidateとして記録する。overrouteはあくまで候補であり、高いrouteによる品質改善を否定した実測結論ではない。未実施ケース一覧を必ず保持する。BLOCKEDは完遂失敗として可視化するが、権限・network障害を推論能力不足やモデル昇格の根拠に変換しない。

### current / new / alwaysSol比較設計

- currentは変更前の3-route policy。phase3では同じ3-routeを保ち、次の段階をnewの候補として加える。always_solは比較用に全自動課題をSol Controllerへ渡すbaselineで、運用policyへの強制変更ではない。
- 同じprompt、受入条件、使い捨て初期checkout、環境、model/effort/tierを固定し、独立sessionで順序を交替して複数回測る。実行順・config/commit・環境を記録してキャッシュ/並行利用差を分離する。
- acceptable route set、実際のRoot spawn role、受入の独立check、初回完遂/再試行/昇格、Rootと全childのtoken・wall timeを比較する。期間集計をtask単位の対比較の代用にしない。
- 既存9件、synthetic unit test、明示roleのSmokeはsemantic live evalではない。この変更ではpaid/live runを実行しない。自然言語分類精度、品質、時間、費用の改善は未検証。費用を比べる場合は既存Metricsの推定条件と観測率を別途報告する。

### 初期Routeと実行中昇格の区別

A3は調査後のAstra昇格があり得ても、初期acceptable routeはSolだけ。`actual_final_route` と `final_route_evidence`、`escalation_events` (from_route/to_route/evidence) は初期routeとは別に保存する。A4は初期証拠の段階からSol/Astra両方が妥当なケースで、複数許容route setを検査する。phase3は旧3-route基準を維持し、低consequenceでhardだけのA5はSol。hard推論へのAstra拡張は後続policy変更として明示的に扱う。
