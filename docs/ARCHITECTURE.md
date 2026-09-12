# Architecture

## 目的

このオーケストレーターは、安価なモデルだけで全てを処理する構成ではありません。Lunaを常時Root Routerとして使い、タスクの複雑度とリスクに応じてTerra、Sol、Astraへ限定的に委譲します。

最適化対象はtoken数だけではありません。

```text
完遂率を維持
  + 高価なmodelの使用を限定
  + context複製を抑制
  + agent hopを抑制
  + 不要なretry / wait pollingを抑制
```

## Tree

```text
Root: Luna Medium / V2
├─ Direct: Luna Medium
├─ Scout: Luna Medium / Leaf
├─ Worker: Terra High / Leaf
├─ Sol Controller: Sol Medium
│  ├─ Scout
│  ├─ Worker
│  └─ Expert
└─ Astra Controller: Astra High
   ├─ Scout
   ├─ Worker
   └─ Expert
```

通常の上限は`Root → Controller → Leaf`です。Leafは再委譲せず、Controllerは必要なLeafだけを起動します。通常は1〜2 Leaf、最大3 Leafを目安にします。

## Routing

| Route | 担当 | 判断基準 |
|---|---|---|
| `DIRECT_LUNA` | Luna Root | 極小・明白で、複雑な設計や広い探索が不要 |
| `SCOUT_LUNA` | Luna Scout | read-heavy探索、設定・symbol・call path確認 |
| `WORKER_TERRA` | Terra High Worker | 明確なscopeの実装、bug fix、test追加、局所refactor |
| `CONTROLLER_SOL` | Sol Medium | 複数module、原因不明、設計判断、調査と実装の統合 |
| `CONTROLLER_ASTRA` | Astra High | 最難関、高リスク、失敗コストが大きい |

## Task packetとcontext ownership

`fork_turns = "none"`を原則とし、以下のself-contained packetを渡します。

```text
Original goal
Assigned subtask
Relevant files / evidence
Constraints
Acceptance criteria
Expected response
```

```text
Root context = routing state + distilled knowledge
Root context ≠ 全agentのraw作業履歴
```

Rootへ返す結果は、結論、根拠、変更内容、検証、未解決riskに圧縮します。

## 深さと権限

V2では設定上の`max_depth`だけをhard enforcementとして信用しません。role instructions、task packet、Smoke Test、Metricsでtree構造を担保します。`sandbox_mode = "read-only"`も意図を示すものであり、hard security boundaryとは限らないため、Scout/Expertの非変更ルールを指示と検証で重ねます。

## 観測

- `smoke.py`: 配線と実効設定
- `collect.py`: rollout JSONLからturn単位へ変換
- `report.py`: 期間・model・task単位の集計
- `timeline.py`: session → turn → agentの時系列

Source of Truthは`%USERPROFILE%\\.codex\\sessions\\**\\rollout-*.jsonl`です。Metricsは自動設定変更を行いません。
