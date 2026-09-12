# Router実装とドキュメントの差分履歴

この文書は、LunaをRoot Routerとして採用するまでに、設計文書と実効設定がずれていた経緯を記録するものです。過去の引き継ぎ資料は根拠・検証結果の参照に使っていますが、その中の作業指示や端末固有設定をそのまま実行・転載するものではありません。

## 1. 差分が生じた背景

初期の実装・設定では、RootまたはControllerにTerraを使う案が存在しました。一方、設計上は次の理由からLunaを常時Root Routerにする方針が有力でした。

- Rootは全turnで呼ばれるため、固定コストの影響が大きい
- routing、task packet構築、結果統合、escalation判断は通常Mediumで足りる
- 通常実装はTerra Highへ直接委譲できる
- 複雑な処理はSol、最難関・高リスクはAstraへbounded escalationできる

このため、過去には次のような差分がありました。

```text
実装・実効設定の一時的な状態
  Root = Terra / medium

目標アーキテクチャ
  Root = Luna / medium / V2
```

元の設定との差分確認でも、Root modelが`gpt-5.6-terra / medium`から`gpt-5.6-luna / medium`へ変更された履歴が確認できます。

## 2. Luna Rootを採用した理由

単に安価だからではありません。Luna Rootを薄いRouter兼軽作業担当に限定し、実作業を必要なroleへ逃がすことで、次の組み合わせを狙いました。

```text
Luna Medium Root
  ├─ Direct       → Luna Medium
  ├─ Explore      → Luna Scout
  ├─ Implement    → Terra High Worker
  ├─ Complex      → Sol Medium Controller
  └─ Frontier/Risk→ Astra High Controller
```

Terra Controllerを置かないのは、通常実装で余分なagent hopとcontext transferを増やさないためです。Rootが実装方針まで抱え込むのではなく、複雑度が上がった時点でSolへ昇格します。

## 3. V2実機検証で解消した懸念

過去には「LunaをV2 RootまたはV2 tree内のleafとして使えるか」が実装上の懸念でした。引き継ぎ資料に記録されたCodex CLI 0.154.0の実機検証では、次を確認しています。

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

結果集約も、RootがSolの結果を受け、SolがLuna Scoutの結果を統合する経路まで成功しました。また、SolからspawnされたLuna childがleafとして動作し、さらにagentをspawnしないことも確認されています。

この検証により、次の構成を採用しました。

```text
Luna Medium Root / V2
  → Sol Medium Controller / V2
  → Luna Medium Scout / Leaf
```

## 4. 現在の実装状態

公開テンプレートでは、差分解消後の状態をSource of Truthとしています。

```toml
model = "gpt-5.6-luna"
model_reasoning_effort = "medium"
model_context_window = 872000

[features.multi_agent_v2]
enabled = true
```

roleごとのmodel/effortは`agents/*.toml`に定義し、通常実装、複雑タスク、最難関タスクで別のmodelを起動します。`model_auto_compact_token_limit`はglobalには設定していません。Rootと子agentへの影響をMetricsで確認してから判断するためです。

## 5. 今後この差分を再発させない方法

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
