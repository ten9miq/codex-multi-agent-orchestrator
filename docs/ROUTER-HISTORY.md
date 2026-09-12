# Router実装とドキュメントの差分履歴

この文書は、LunaをRoot Routerとして利用できるかを調査した経緯と、調査前後の設計判断を記録するものです。過去の引き継ぎ資料は根拠・検証結果の参照に使っていますが、その中の作業指示や端末固有設定をそのまま実行・転載するものではありません。

## 1. 設計の出発点

出発点から、設計上の基本方針は「Lunaを常時Root Routerとして使う」ことでした。Rootは全turnで呼ばれるため、次の理由でLuna MediumをRootに置くのがコスト最適化の中心でした。

- Rootは全turnで呼ばれるため、固定コストの影響が大きい
- routing、task packet構築、結果統合、escalation判断は通常Mediumで足りる
- 通常実装はTerra Highへ直接委譲できる
- 複雑な処理はSol、最難関・高リスクはAstraへbounded escalationできる

ただし、設計と実装の間には次の不確実性がありました。

```text
設計上の目標
  Luna Medium Root Router

調査前の不確実性
  Lunaがsubagentを使えるか
  Luna RootからV1/V2の子を起動できるか
  V2 ControllerからLunaをleafとして起動できるか
```

この不確実性から、TerraをRootやControllerに使う代替案も検討されました。しかし、それは設計方針を変更した結果ではなく、Luna案がruntime制約で成立しない場合に備えた暫定候補でした。

## 2. 調査前に生じた誤解

途中で「Lunaではsubagentを使えない」「Luna RootはController構成と相性が悪い」と解釈され、Terra Routerへ変更する案が出ました。これは正確ではありません。

問題はLunaそのものにsubagent機能がないことではなく、Multi-Agent runtimeの世代と親子モデルの互換性でした。特に、V2 ControllerからV1 Lunaを子にする経路には制約・不具合報告があり、次の2つを区別する必要がありました。

```text
Luna Rootがsubagentを使えるか
  ≠
V2 ControllerがV1 Luna childを起動できるか
```

この区別が文書と実装の説明を一時的にずらした主因です。

## 3. 調査で確認したこと

引き継ぎ資料に記録されたCodex CLI 0.154.0の実機検証では、LunaをRootとしてMulti-Agent V2で動作させられることを確認しました。さらに、RootからSol Controllerを起動し、SolがLuna childを起動するnested delegationと結果集約も確認しました。

```text
Luna Medium Root / V2
    ↓
Sol Medium child / V2
    ↓
Luna Medium grandchild / V2 leaf
```

確認された実効値:

```text
gpt-5.6-luna / medium / v2   Root
gpt-5.6-sol  / medium / v2   Controller
gpt-5.6-luna / medium / v2   Leaf
```

結果集約とLuna childのleaf動作まで確認できたため、少なくとも検証環境ではLuna Root案を捨てる必要がないと判断しました。

### V1/V2互換性に関する注意

調査資料では、V2 ControllerからV1 Lunaを子にする経路に制約・不具合報告があり、Controller配下をV2 modelだけにする代替案も検討されています。一方、実機検証ではLuna childがV2 tree内のleafとして起動し、nested delegationが成功しました。

したがって、ここでの結論は「Luna childが常に利用可能」という一般保証ではありません。Codex runtimeやmodel catalogを更新した場合は、`smoke.py --expect controller_sol_scout`で実効treeを再確認します。失敗する場合は、Controller配下の探索roleをTerra系へ切り替える判断を別途記録します。

## 4. 調査後の採用アーキテクチャ

単に安価だからではなく、Luna Rootを薄いRouter兼軽作業担当に限定し、実作業を必要なroleへ逃がします。

```text
Luna Medium Root
  ├─ Direct       → Luna Medium
  ├─ Explore      → Luna Scout
  ├─ Implement    → Terra High Worker
  ├─ Complex      → Sol Medium Controller
  └─ Frontier/Risk→ Astra High Controller
```

Terra Controllerを置かないのは、通常実装で余分なagent hopとcontext transferを増やさないためです。Rootが実装方針まで抱え込むのではなく、複雑度が上がった時点でSolへ昇格します。

## 5. 現在の実装状態

公開テンプレートでは、差分解消後の状態をSource of Truthとしています。

```toml
model = "gpt-5.6-luna"
model_reasoning_effort = "medium"
model_context_window = 872000

[features.multi_agent_v2]
enabled = true
```

roleごとのmodel/effortは`agents/*.toml`に定義し、通常実装、複雑タスク、最難関タスクで別のmodelを起動します。`model_auto_compact_token_limit`はglobalには設定していません。Rootと子agentへの影響をMetricsで確認してから判断するためです。

## 6. 今後この差分を再発させない方法

設定変更時は、設計文書だけでなく次の3層を同時に確認します。

1. `config.example.toml`のRoot modelとMulti-Agent V2設定
2. `agents/*.toml`のrole model/effortと委譲禁止ルール
3. rollout JSONLの`turn_context`、`agent_role`、親子関係

確認手順:

```text
設定変更
  ↓
新規sessionでSmoke Test
  ↓
rollout JSONLの実効値確認
  ↓
collect.py / report.pyでtoken・完遂率・昇格率を確認
  ↓
設計文書と実装の差分を更新
```

モデルやCodex runtimeを更新した場合は、ここに記録した実機検証を永続的な保証とみなさず、Luna Root、Sol nested delegation、Luna leaf behaviorを再検証します。
