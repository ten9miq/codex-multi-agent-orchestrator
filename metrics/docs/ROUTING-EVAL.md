# Routing回帰確認とSol effort比較

## 自然言語Routingの回帰ケース

Smoke Testはmodel/roleの配線確認であり、下表の意味判断を自動判定しない。安全な使い捨てrepositoryと新規sessionで実行し、各turnについて「期待Route・実際に選んだRoute・spawn role・結果の検証・根拠」を記録する。RootのRouteはモデル名やtool未使用だけから確定しない。曖昧なケースを自動的に成功扱いしない。

| ID | 依頼の形 | 期待Route | 確認点 |
|---|---|---|---|
| D1 | 本文を全て貼り、「この文章を短くして」 | `DIRECT_LUNA` | 外部資料の探索が不要 |
| D2 | コード本文を全て貼り、「このコードの意味を説明して」 | `DIRECT_LUNA` | repository内の実装調査を要求していない |
| S1 | 「このrepositoryでfoo関数の呼び出し元を調べて」 | `SCOUT_LUNA` | symbol探索が必要 |
| S2 | 「現在の実装と過去commitを比較して」 | `SCOUT_LUNA` | 履歴との照合が必要 |
| S3 | 「設定値がどこで定義されているか調べて」 | `SCOUT_LUNA` | 設定元が不明 |
| W1 | path・該当行・変更前後の文字列を完全指定したtypo修正 | `DIRECT_LUNA`または`WORKER_LUNA` | 完全指定でも実ファイルの確認・編集範囲によって判断する |
| W2 | 既知の1ファイルで再現条件と期待挙動が明確なbug修正 | `WORKER_LUNA` | 挙動変更と検証が必要 |
| W3 | 既知の複数ファイルにまたがる難しいが境界明確な実装 | `WORKER_SOL` | 未知の原因分析は主作業ではない |
| C1 | 原因不明の複数moduleの不具合を調べ、修正方針を決める | `CONTROLLER_SOL` | 調査結果の統合と設計判断が必要 |

特にS1～S3とW2が`DIRECT_LUNA`に流れないことを確認する。実際の動作を伴うW1～W3は使い捨てrepositoryで行い、差分と必要なテストを確認する。Astraのケースは通常の回帰実行から外し、必要時だけ別途確認する。

## `worker_sol` Medium / High 比較

`worker_sol`の標準effortは、tokenコストを考慮して暫定的にMediumとする。比較時は同じ受入条件を持つ複数の実装課題を選び、MediumとHighを別session・独立checkoutで実施する。role fileのeffortだけを切り替え、model、課題、環境、速度設定は合わせる。

各課題で検証PASS、受入条件の達成、人手で確認した手戻り、Rootとchildを合計したtoken、API USD換算、Codex追加クレジット換算、所要時間を記録する。`first_pass_success`はrollout由来の推定値であり、成果物の合否の代わりにしない。失敗後の再試行や昇格は同じ課題の総量に含める。追加クレジット換算からプラン内利用枠の減少量を推定しない。

Mediumで品質と手戻りが同程度なら、Mediumを維持する。Highが検証失敗や手戻りを明らかに減らす課題の特徴が判明したら、その条件に限ってHighを使う。現時点では品質・総費用への効果を実測済みとは扱わない。
