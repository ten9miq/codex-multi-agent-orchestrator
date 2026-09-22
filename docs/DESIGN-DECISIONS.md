# Design Decisions

## RootをLuna Mediumにする

Rootは全turnで呼ばれるため、GPT-6 Luna Mediumを使います。通常実装はLuna High Worker、難しい既知の実装はSol High Worker、設計統合はSol Controller、最難関はAstra Controllerが担当します。

## 通常実装はLuna High Workerにする

通常実装のscopeと受入条件が明確なら、Rootから `worker_luna` へ直接委譲します。難しいが境界の明確な実装には `worker_sol` を選びます。GPT-6 Lunaの能力向上はWorkerの担当範囲に反映し、Directの成立条件は広げません。実運用の完遂率、手戻り、枠消費を観測して境界を調整します。

## effortの順次試行をしない

Luna Medium、Luna High、Sol High、Sol XHigh、Astraを毎回順に試すと不要なagent起動と再作業が生じます。依頼時点の証拠で適切なRouteを選び、核心の不確実性が判明した場合だけ昇格します。Sol XHigh ExpertはController配下の難しい局所分析・レビューに限定します。

## Leafの再委譲を禁止する

Scout、Luna/Sol Worker、Sol ExpertはLeafです。Leafの再委譲を許すとtree、cost、責任範囲が予測しにくくなります。Controllerだけが必要なLeafを起動します。

## `fork_turns = "none"`を原則にする

full historyの複製はchildの入力tokenと無関係な文脈を増やします。必要情報はtask packetで明示します。partial/full forkはpacketだけでは安全に再現できない場合に限定します。

## context windowとauto-compactを分離する

`catalog.context_window`（モデルの標準値）は272K、`catalog.max_context_window`（configで指定できる上限）は872Kです。`model_context_window`（config.tomlの指定値）を872000にしてheadroomを広げます。一方、`model_auto_compact_token_limit`をglobalに設定すると子Controllerにも影響し、複雑なSol/Astra作業を早期compactさせる可能性があります。そのため現時点ではcontext windowだけを設定し、compact閾値はMetrics実測後に判断します。

## 結果を圧縮して返す

window拡張だけでは、Leafのraw outputをRootへ返すことで効果が失われます。Controllerをcontext compressorとして扱い、Rootには統合済みの必要知識だけを返します。role別の必須項目は各agent TOMLに定義しています。

## Metricsは受動解析にする

Metricsが設定を自動変更すると、原因と結果の切り分けが難しくなります。rollout JSONLを読み、変更前後のtoken、完遂率、昇格率、context使用量を比較してから人間が変更を決めます。

wait/status pollingのtimeoutを20分/25分へ変更した理由と評価指標は[`WAIT-POLLING.md`](WAIT-POLLING.md)に分離して記録しています。

## 変更時の評価

設計変更では、変更理由だけでなく、token cost、完遂率、latency、context使用量への影響とrollback条件を記録します。新しいbest practiceを採用する場合も、既存構成の不変条件を確認してからSmoke TestとMetricsで評価します。
