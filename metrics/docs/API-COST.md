# 根拠付きAPI token料金推計

`api_cost.py` は **request単位の根拠が揃った場合だけ** 短文脈・長文脈を分類する独立APIです。
`api-pricing-2026-10-03.json` は公式資料を2026-10-03に確認した固定snapshotです。
API料金、Codex追加クレジット、ChatGPT Proプラン内利用枠は別物です。

## 現在できること・できないこと

- 正規化した実request証拠に対し、同一requestのinput/cache/model/実効tierを検証してtoken料金を推計できます。
- 現行`collect.py`のturn集計には、検証済みのrequest帰属・cache write内訳がありません。実ログのrequest adapterは未実装です。実行環境の個人rolloutを取得した・計測したという意味ではありません。
- `rollout_cost_coverage(rows)` は全turnを未分類とし、request数は`UNKNOWN`に相当する`unknown_request_count=True`を返します。0/Nは**turn単位の分類可能件数**であり、request coverage 0%を測定した値ではありません。
- `last_token_usage`、session累積、turn totals、context peak、`last_token_usage.total_tokens`、設定windowはrequest課金判定に使いません。272Kを超える累積使用量だけでは、長文脈requestがあったとは言えません。
- reportの明示的tierシナリオは短文脈基準の参考値です。実効tier、長文脈判定、cache write、model切替の請求帰属を検証した実測額ではありません。既定では不足根拠を未分類として残します。

## 公式に確認したルールと範囲

確認日: **2026-10-03**。価格の履歴上の適用開始日・終了日は確認していません。過去の使用量をこのsnapshotで推計する場合も、当時の請求額の再現ではありません。

| model | 公式のmodel別根拠 | Standard短文脈: input / cached read / cache write / output (USD/1M) |
|---|---|---|
| GPT-6 Luna | https://developers.openai.com/api/docs/models/gpt-6-luna | 0.10 / 0.01 / 0.125 / 0.50 |
| GPT-6.1 Sol | https://developers.openai.com/api/docs/models/gpt-6.1-sol | 2.00 / 0.10 / 2.50 / 10.00 |
| GPT-6 Astra | https://developers.openai.com/api/docs/models/gpt-6-astra | 10.00 / 1.00 / 12.50 / 50.00 |

上記**各model pageのPricing節**で、次をそれぞれ確認しました。

- requestのinputが**272,000 tokenを厳密に超える場合**、そのrequest全体のinput・cache料金は短文脈の2倍、output料金は1.5倍。ちょうど272,000は短文脈です。
- cache writeのStandard短文脈単価はuncached inputの1.25倍です。
- Fastは該当Standard料金の2倍です。長文脈では長文脈料金へFast倍率を適用します。

[公式API Pricing](https://developers.openai.com/api/docs/pricing) のStandard/Fast各表でも短文脈・長文脈の各単価を照合しました。例えばGPT-6.1 SolのFast長文脈はinput 8.00 / cached read 0.40 / cache write 10.00 / output 30.00 USD/1Mです。

[公式Prompt cachingの料金説明・Calculate input cost節](https://developers.openai.com/api/docs/guides/prompt-caching) は、inputを通常入力・cache read・cache writeの排他的な内訳として計算します。cache writeを通常入力に重ねて加算しません。input合計にはcache read/writeを含むため、長文脈閾値を判定する際にcache分を差し引きません。

[公式Reasoning modelsのusage例](https://developers.openai.com/api/docs/guides/reasoning) ではreasoningはoutputの内訳です。`output_tokens`に含まれるため二重加算しません。

snapshotは上記3modelのStandard/Fastだけを検証対象とします。旧model、model alias/snapshot suffix、Flex/Batch/Ultrafastへ暗黙に単価を流用しません。既存`cost-weights.json`やarchived Terra資料は変更していません。

### 明示した除外

この推計はAPI text-tokenの基準単価によるものです。regional/FedRAMP割増、tool料金、その他の従量料金、割引・税・請求調整を再現せず、実際の請求額を示しません。APIの長文脈倍率やFast倍率からCodex追加クレジットやChatGPT Pro利用枠の減少量を推定しません。特にCodex製品側のmodel別例外とAPI料金表は同一のルールではありません。

## Python API

入力はこのmodule専用の正規化interfaceであり、Codex rolloutに存在すると主張するschemaではありません。

- `Evidence(request_id, source, scope, origin)`: usage/model/tierそれぞれの証拠を同じ実request IDへ結び付けます。`scope="request"`, `origin="response"`と空でないsource参照が必要です。設定や送信時のrequested値では足りません。
- `RequestUsage`: dataset内で一意な安定request ID、model、実効tier、input、cached input、cache write、output、上記3種のEvidenceを渡します。IDがsource内だけで一意ならadapterがnamespaceを付けます。
- cache write/readの欠損は0へ補完しません。明示的0も観測証拠が必要です。input/cache/outputは非負整数、cache read + write ≤ input、reasoning ≤ outputを検証します。
- `estimate_requests(usages, catalog=None)`: modelごとの閾値とtier別の料金snapshotを適用します。catalog省略時は同梱snapshotを読みます。source/date/version/料金欠損・不正catalogは例外、根拠不足requestは`unclassified`と`usd=None`です。
- 結果には短文脈/長文脈の件数、未分類観測数、識別不能record数、重複除外件数、`classified_usd_subtotal`、使用version/dateを含みます。未分類が残れば小計を全体料金とは呼びません。全件未分類・空入力の小計は0ではなく`None`です。

同じrequest IDと完全に同じ観測の再出現は1回だけ計上します。同じIDの矛盾するusage/model/tierはそのID全体を除外します。token数が同じでも別IDなら別requestです。compaction eventは課金requestを生成せず、重複判定をresetしません。streaming途中のusageとfinal usageのような更新を自動で推測・マージせず、adapter側で最終証拠を解決する必要があります。

将来adapterを追加するときは、許可された実ログでrequest ID・usage・model・実効tierの対応、cache write記録、streaming再送・fork/compaction挙動を確認し、fixture化してから接続します。未知のfield名を推測して`RequestUsage`へ昇格させません。

## 検証

```sh
python -m unittest discover -s metrics -p "test_api_cost.py" -v
python -m unittest discover -s metrics -p "test_*.py"
python -m unittest discover -s scripts -p "test_*.py"
```

合成された正規化API入力で、閾値未満・等値・超過、cacheを含む閾値、cache writeのpartition、短い2requestの累積、model/tier混在、重複usage、compaction後再出現、missing evidence、reasoning二重計上防止、旧turn未分類を検証します。合成fixtureの成功を実rolloutの観測coverageと混同しません。
