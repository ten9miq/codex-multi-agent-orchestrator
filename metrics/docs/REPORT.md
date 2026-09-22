# レポート

収集後に実行します。

```powershell
python "$env:USERPROFILE\.codex\metrics\report.py"
python "$env:USERPROFILE\.codex\metrics\report.py" --days 7
python "$env:USERPROFILE\.codex\metrics\report.py" --since "2026/09/01 00:00" --until "2026-09-08T00:00:00+09:00"

# 同じ終了時刻を固定して24時間・7日を比較する例
$Until = Get-Date
python "$env:USERPROFILE\.codex\metrics\report.py" --since $Until.AddHours(-24).ToString("o") --until $Until.ToString("o")
python "$env:USERPROFILE\.codex\metrics\report.py" --since $Until.AddDays(-7).ToString("o") --until $Until.ToString("o")
```

| option | 内容 | 既定値 |
|---|---|---|
| `--input PATH` | Metrics JSONL | `~/.codex/metrics/routing-metrics.jsonl` |
| `--days N` | 集計する直近日数（1以上） | `30` |
| `--since DATETIME` | 開始日時（含む） | なし |
| `--until DATETIME` | 終了日時（含まない） | なし |
| `--weights PATH` | 重み付きコスト設定 JSON | `~/.codex/metrics/cost-weights.json` |

入力がなければ `collect.py` の実行を案内します。壊れた JSONL 行は数だけ表示して、読めた行から集計します。

`--since` / `--until` は ISO 8601、`YYYY/MM/DD HH:MM[:SS]`、または
`YYYY-MM-DD HH:MM[:SS]` を受け付けます。offset付きの値はそのoffsetを保ち、
offsetなしは実行PCのローカル時刻として解釈した後、UTCへ正規化して比較します。
範囲は常に `[since, until)`（開始を含み、終了を含まない）です。`--since` 単独は
現在まで、`--until` 単独はその30日前からを集計します。`--days` は明示的な範囲指定と
併用できません。レポート先頭の対象期間行で、実際に用いたUTCの範囲を確認できます。

変更前後の比較では、`--days`を別々の時刻に実行する代わりに、上記のように同じ
`--until` を固定し、各期間を明示してください。これにより24時間と7日の各レポートが
再実行時にずれることを避けられます。

## 出力セクション

| セクション | 項目の意味 |
|---|---|
| 冒頭 | `対象期間` はUTCに正規化した半開区間。`Root turn数` は Root 行数、`ユニークRoot thread/session数` は同じ会話内の複数turnを重複計上しない数、`全モデルturn数` は対象期間の Root/child を含む行数、`解析不能JSONL行` は無視した入力行数。 |
| 初期ルート | Root の `initial_route` 分布。Root がなければその旨を表示。 |
| Route判定の出所 | `route_source`別の件数と、モデルから推定された`DIRECT_LUNA`件数を表示する。GPT-6 Luna Rootがツールを使いagentを起動しなかったturnは、明示Routeがない限り`UNKNOWN`にする。model推定は明示的なDirect選択の証明ではない。 |
| 実行モード | `ORCHESTRATED_ROUTE` と `LEGACY_ROOT_MODEL` を分ける。旧Metricsでfieldがなければ`UNKNOWN`であり、legacyと断定しない。 |
| Route別 品質・task使用量 | 初期RouteごとのRoot turn数を分母に、完了率、初回完遂率、後方互換の手戻り候補率、追加要求・モデル訂正・不明のcue分類率、Rootと帰属subagentを合算した平均/P90 token、weight設定時の平均weighted costを表示する。GPT-6 Luna Rootがツールを使いagentを起動しない要確認件数も表示する。 |
| 品質 / 手戻り分類 / 検証結果 | `完了率` は Root の `status=COMPLETE`、`初回完遂率` は `first_pass_success`。`rework_class` は次Root turnのcueを `NONE` / `USER_FOLLOWUP` / `MODEL_CORRECTION` / `UNKNOWN` に分類し、Root turn別と会話内（Root thread/session）で表示する。`possible_immediate_rework` は旧heuristicとして併記する。検証結果は全Rootと初期Route別に `PASS` / `FAIL` / `NOT_RUN` / `UNKNOWN` を欠損と区別して表示する。 |
| 完了状態・検証の観測範囲と出所 | `status_source`を`assistant_message`、`task_complete.last_agent_message`、通常完了の`completion_event`、`UNKNOWN`別に表示する。`verification_source`を持つRoot数をcoverageとして示し、`FAIL / coverage`の既知内FAIL率と`FAIL / 全Root turn`の全Root FAIL率を別表示する。完了eventは`verification`を補完せず、旧Metricsまたはprotocolなしは`UNKNOWN`である。 |
| Agent返却結果サイズ | assistant 結果と `USER_RESULT` の推定 token（文字数÷4）の平均・中央値・P90・最大。`USER_RESULT` がない既存 rollout は0。 |
| 昇格 | `Luna Worker → Sol` は初期 Route が `WORKER_LUNA` の Root のうち `escalation_count >= 1`、`Sol Worker → Controller` は初期 Route が `WORKER_SOL` で最終 Route が `CONTROLLER_SOL`、`Sol Controller → Astra` は初期 Route が `CONTROLLER_SOL` で最終 Route が `CONTROLLER_ASTRA`。全Rootの昇格率と `initial_route → effective_route` の遷移件数も表示する。 |
| モデル別使用量 | 実効 model ごとの turn 数、input、cached input、output、reasoning、total token。total の多い順。 |
| Auto Review | `codex-auto-review` のturn、token内訳、cached比率、全tokenに占める比率、weight設定時のcostを通常のRouting taskと分けて表示する。 |
| Context peak | turnごとの `last_token_usage` から得たcontext peakをmodel別に表示する。累積input tokenやtask total tokenとは別指標。 |
| Context window 分布 / Compaction | 実効window値ごとのturn数と、`compacted` event数・発生時に観測できたcontextを表示する。旧rolloutにevent/snapshotがなければcountは0、contextは`UNKNOWN`であり、compaction非発生とは断定しない。 |
| タスク単位token分布 | `(root_thread_id, root_turn_id)` ごとに Root と帰属 subagent の total token を合算し、平均・中央値・P90を表示。 |
| Coordination / 待機 | `キャッシュ入力比率` は cached input / input、`wait/status系tool call` は call 数、`status-only token` はその token と全 total に占める割合。 |
| 重み付きコスト | 設定済みなら全 turn の合計とタスク平均、使用した設定パス。未設定なら無効と表示。 |

## 重み付きコスト

現在の `cost-weights.json` は公開API価格を参考weightとして設定しています。値は100万 token あたりです。以下は旧構成の相対weightの例であり、現在の設定値ではありません。

```json
{
  "models": {
    "gpt-5.6-luna": {"input": 1.0, "cached_input": 0.1, "output": 6.0, "reasoning": 6.0},
    "gpt-5.6-terra": {"input": 2.5, "cached_input": 0.25, "output": 15.0, "reasoning": 15.0}
  }
}
```

`weighted cost` は API料金または Codex subscription credit そのものとは限りません。比較用の相対 weight です。

## サンプルレポートの読み方

次は旧構成で作成した、個人パスを省略した2日分の履歴サンプルです。現在の役割・モデル構成の出力例ではありません。

```text
Codex ルーティングレポート（直近 2 日）
Rootタスク数                         74
全モデルturn数                      188

■ 初期ルート
  DIRECT_LUNA                        59
  WORKER_TERRA                        8
  SCOUT_LUNA                          4
  CONTROLLER_SOL                      2
  CONTROLLER_ASTRA                    1

■ 品質
  完了率                            98.6%
  初回完遂率                        89.2%
  即時手戻り候補率                  10.8%
  検証失敗率                         0.0%

■ モデル別使用量
  gpt-5.6-luna         81 turns   total 43,783,841
  gpt-5.6-terra         8 turns   total  6,844,771
  codex-auto-review    94 turns   total  6,373,570
  gpt-5.6-sol           4 turns   total  1,304,868
  gpt-6-astra           1 turn    total     70,865

■ タスク単位token分布
  平均                          702,761
  中央値                        504,516
  P90                         1,631,901

■ Coordination / 待機
  キャッシュ入力比率                95.4%
  wait/status系tool call             64
  status-only token                   0

■ 重み付きコスト
  合計                              5.8308
  タスク平均                        0.0788
```

## サンプルからの判断例

### 1. 完了率と初回完遂率

```text
完了率       98.6%
初回完遂率   89.2%
```

完了率は高く、最終的に完了できているタスクが多い状態です。一方、初回完遂率との差が約9.4ポイントあるため、次のどれかが発生しています。

- escalationしてから完了した
- retryが発生した
- Root完了後に訂正らしい次turnがあった

この差が大きくなった場合は、単純に高価なmodelへ置き換えるのではなく、まず`WORKER_TERRA`の失敗例と`possible_immediate_rework`の実例を確認します。

### 2. 初期ルートの分布

旧サンプルでは74 Root taskのうち59件が`DIRECT_LUNA`です。旧集計はモデルからの推定を含むため、この件数だけでDirectの成立条件を満たしたとは判断できません。

この比率が高いこと自体は問題ではありません。次を合わせて判断します。

- 完了率が高いか
- 初回完遂率が落ちていないか
- Rootのtask tokenが大きくなっていないか

現行構成では、GPT-6 Luna Rootがツールを使い、agent起動も明示Routeもないturnは `UNKNOWN` として扱います。`DIRECT_LUNA` の件数に加え、探索・変更がDirectへ流れていないかを具体的なturnで確認します。

### 3. 昇格率0%の読み方

```text
Terra → Sol   0/8
Sol → Astra   0/2
```

これは「昇格が不要だった」可能性と、「複雑なtaskが十分含まれていなかった」可能性の両方があります。0%だけを成功指標にしません。

Terraの初回完遂率、手戻り、task単位P90 tokenを併せて確認します。Terraで失敗しているのにSol昇格が0%なら、昇格条件またはRoot/Workerのprotocolが機能していない可能性があります。

### 4. モデル別使用量とAuto Review

`codex-auto-review`は通常のRoot/Child routingとは別の内部補助処理です。モデル別合計に含まれるため、オーケストレーター自身のコストを評価するときは、次の2通りを分けて見る必要があります。

```text
全体運用コスト      codex-auto-reviewを含める
Routing設計の比較   Root/Child roleだけを見る
```

このサンプルではLunaのturn数とtotal tokenが大きいため、Luna Rootのcontext再利用・cached input比率・task単位tokenを重点的に確認します。

### 5. タスク単位token分布

```text
中央値   504,516
P90    1,631,901
```

平均だけでなく中央値とP90を見ます。平均よりP90が大きく離れている場合、一部の長いtaskが全体コストを押し上げています。

次に確認するもの:

- P90 taskのroute
- そのtaskのsubagent数
- context peak
- wait/status call
- retry/escalation

改善対象は平均的なtaskではなく、まずP90のlong tailです。

### 6. キャッシュ入力比率

```text
キャッシュ入力比率 95.4%
```

同じ入力の大部分がcache対象になっていることを示します。高いほど常に安いとは限りませんが、長いRoot contextが毎回完全な新規inputとして扱われている状態ではない、という材料になります。

一方、cached inputが多くてもtask単位tokenが大きければ、不要な履歴・tool結果・subagent結果をRootへ蓄積している可能性があります。context peakと返却結果サイズを併せて確認します。

### 7. wait/status系tool call

```text
wait/status系tool call 64
status-only token       0
```

call数は多いものの、現在のparserが`status-only`と判定したturnのtokenは0という意味です。これは「waitのコストが完全に0」という意味ではありません。

wait callが通常の作業turn内に含まれている場合、tokenは通常turnへ計上されます。wait timeout変更の効果は、変更前後で次を比較します。

- wait call数
- status-only turn数
- task duration
- task total token
- 完遂率

### 8. Agent返却結果サイズ

assistant結果の平均・中央値・P90・最大と、`USER_RESULT`の同じ値を比較します。

```text
assistant結果  平均180 / 中央値78 / P90 410 / 最大1,518
USER_RESULT   平均20  / 中央値0  / P90 0   / 最大967
```

通常の結果が短く、ControllerがRootへ返す情報を圧縮できているかを見る指標です。最大値だけが大きい場合は、そのtaskのraw outputや長文引用を確認します。既存rolloutで`USER_RESULT`がない場合は0なので、古い期間との単純比較には注意します。

## 重み付きコストの使い方

### これは料金ではない

`cost-weights.json`の値は、モデル・token種別ごとの相対的な重みです。たとえば、Lunaのinputを`1.0`、Terraを`2.5`と置けば、同じ1M uncached input tokenを使ったときの相対的な負荷を比較できます。

```json
{
  "models": {
    "gpt-5.6-luna": {
      "input": 1.0,
      "cached_input": 0.1,
      "output": 6.0,
      "reasoning": 6.0
    },
    "gpt-5.6-terra": {
      "input": 2.5,
      "cached_input": 0.25,
      "output": 15.0,
      "reasoning": 15.0
    }
  }
}
```

現在の計算は概念的に次です。

```text
uncached_input = input_tokens - cached_input_tokens

weighted cost = (
    uncached_input × input weight
  + cached_input_tokens × cached_input weight
  + output_tokens × output weight
  + reasoning_tokens × reasoning weight
) / 1,000,000
```

### 重みの決め方

実際の料金表を再現したい場合は、同じ期間・同じ単位で次を設定します。

- input: 100万uncached input tokenあたりの相対値
- cached_input: 100万cached input tokenあたりの相対値
- output: 100万output tokenあたりの相対値
- reasoning: 100万reasoning tokenあたりの相対値

料金を再現しない場合は、基準modelを`1.0`にして比較用の任意weightとして構いません。

### reasoning tokenの注意

Backendや料金体系によっては、`reasoning_tokens`がoutputの内訳として扱われる場合があります。その場合、output weightとreasoning weightを両方加えると二重計上になります。

比較目的なら、まず次のどちらかを明示します。

```text
reasoningをoutputとは別コストとして見る
  → reasoning weightを設定

reasoningをoutputの内訳として見る
  → reasoning weightを0または未使用にする
```

### サンプル値の判断

```text
重み付きコスト合計  5.8308
タスク平均          0.0788
```

この数値単体に「高い」「安い」という絶対的な意味はありません。次のように同じweightで比較します。

```text
変更前の7日間 weighted cost
変更後の7日間 weighted cost
変更前後の完遂率・初回完遂率
変更前後のP90 task token
```

例えば、Terra Highを増やしてweighted costが増えても、初回完遂率が上がり、P90 task tokenと手戻りが下がるなら、タスク単位では改善の可能性があります。逆にcostだけ下がって完遂率が下がる変更は採用しません。

## 判断の基本手順

レポートを見たら、次の順で確認します。

1. 完了率と初回完遂率
2. 初期Routeと昇格率
3. タスク単位tokenの中央値/P90
4. context peakと返却結果サイズ
5. wait/status callとstatus-only token
6. model別tokenとweighted cost

単一の数値だけでmodelやtimeoutを変更せず、変更前後の同じ期間・同じ定義で比較してください。
