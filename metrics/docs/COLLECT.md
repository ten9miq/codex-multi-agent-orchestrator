# 収集・キャッシュ・token accounting

`collect.py` は `%USERPROFILE%\.codex\sessions\**\rollout-*.jsonl` を読み、turn ごとの `routing-metrics.jsonl` を作ります。

```powershell
python "$env:USERPROFILE\.codex\metrics\collect.py"
```

```text
Codex Metrics収集完了
  rolloutファイル数 : 125
  turnレコード数     : 418
  出力先             : C:\Users\...\.codex\metrics\routing-metrics.jsonl
  状態ファイル       : C:\Users\...\.codex\metrics\state.json
```

## options

| option | 内容 |
|---|---|
| `--codex-home PATH` | Codex home。既定は `CODEX_HOME` または `~/.codex` |
| `--output PATH` | 出力 JSONL。既定は `<CODEX_HOME>/metrics/routing-metrics.jsonl` |

```powershell
python "$env:USERPROFILE\.codex\metrics\collect.py" `
  --output "D:\tmp\routing-metrics.jsonl"
```

## キャッシュ

出力先と同じディレクトリの `state.json` に、rollout ファイルごとの size と mtime、および解析済み turn を保存します。signature が不変の rollout は再解析せず cache から復元します。変更済み・新規 rollout は再解析し、消えた rollout の cache は除去します。state の version が合わない、または読めない場合は安全に全体を再解析します。

出力は毎回 `routing-metrics.jsonl` を再構築し、JSONL は UTF-8/LF で書きます。解析中の JSONL の末尾不完全行は無視され、次回の収集で再取得されます。

## token accounting

subagent rollout には fork 元の過去履歴・token 記録が含まれる場合があります。そのため JSONL 中の全 `token_count` を単純加算しません。

- live turn 開始後の usage を対象にする
- cumulative token snapshot の重複を除外する
- `last_token_usage` を turn ごとに加算する
- Root / Child / Grandchild を `parent_thread_id` で関連付ける
- Child usage を該当 Root task に帰属する

この設計により、一般的な全 token 値の合計より fork tree での過大集計を避けます。raw token は常に保存するため、重みを変更しても過去の Metrics を再計算できます。

## 即時手戻り分類と返却サイズ

同一Root threadで前turn完了後30分以内に始まる次Root turnについて、次turn開始前のuser messageを前turnの`rework_class`へ転記します。値は `NONE`（cueなし）、`USER_FOLLOWUP`（明確な追加依頼）、`MODEL_CORRECTION`（明確な誤り訂正）、`UNKNOWN`（修正・再実行など対象を断定できないcue）です。raw user textはMetricsに保存しません。

`possible_immediate_rework` は後方互換の旧heuristicです。新しい分類とは別に、従来のcue patternだけで値を維持します。そのため新規分類が`UNKNOWN`でも旧fieldがfalseとなる場合があります。いずれもcueに基づく観測であり、原因の確定値ではありません。

assistant 返却本文と `USER_RESULT_BEGIN`〜`USER_RESULT_END` 内を文字数で測り、比較用に文字数÷4の `result_estimated_tokens` と `user_result_estimated_tokens` を保存します。既存 rollout や契約外の返却は0であり、この値だけで過去の返却が圧縮済みとは判定しません。
