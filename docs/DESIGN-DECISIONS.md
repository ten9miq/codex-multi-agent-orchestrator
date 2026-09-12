# Design Decisions

## RootをLuna Mediumにする

Rootは全turnで呼ばれるため、routingに必要な能力を満たす範囲で固定コストを下げます。難しい処理はTerra、Sol、Astraへ委譲します。Rootを高価なmodelにして全作業を抱え込ませる設計は採用しません。

## Terra Controllerを置かない

通常実装は`Luna → Terra High Worker`で十分です。Terra Controllerを挟むとagent hopとcontext transferが増え、複雑さが本当に必要な場合との境界も曖昧になります。その場合はSol Controllerへ昇格します。

## Leafの再委譲を禁止する

Scout、Terra Worker、Sol ExpertはLeafです。Leafの再委譲を許すとtree、cost、責任範囲が予測しにくくなります。Controllerだけが必要なLeafを起動します。

## `fork_turns = "none"`を原則にする

full historyの複製はchildの入力tokenと無関係な文脈を増やします。必要情報はtask packetで明示します。partial/full forkはpacketだけでは安全に再現できない場合に限定します。

## context windowとauto-compactを分離する

`model_context_window = 872000`はheadroomを広げる候補です。一方、`model_auto_compact_token_limit`をglobalに設定すると子Controllerにも影響し、複雑なSol/Astra作業を早期compactさせる可能性があります。そのため現時点ではcontext windowだけを設定し、compact閾値はMetrics実測後に判断します。

## 結果を圧縮して返す

window拡張だけでは、Leafのraw outputをRootへ返すことで効果が失われます。Controllerをcontext compressorとして扱い、Rootには統合済みの必要知識だけを返します。role別の必須項目は各agent TOMLに定義しています。

## Metricsは受動解析にする

Metricsが設定を自動変更すると、原因と結果の切り分けが難しくなります。rollout JSONLを読み、変更前後のtoken、完遂率、昇格率、context使用量を比較してから人間が変更を決めます。

wait/status pollingのtimeoutを20分/25分へ変更した理由と評価指標は[`WAIT-POLLING.md`](WAIT-POLLING.md)に分離して記録しています。

## 変更時の評価

設計変更では、変更理由だけでなく、token cost、完遂率、latency、context使用量への影響とrollback条件を記録します。新しいbest practiceを採用する場合も、既存構成の不変条件を確認してからSmoke TestとMetricsで評価します。
