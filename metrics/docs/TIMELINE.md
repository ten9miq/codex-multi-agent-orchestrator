# Timeline

`timeline.py` は集計済み Metrics を読み取り専用で表示します。既定は `root_thread_id` → `root_turn_id` → `parent_thread_id` / `thread_id` の tree です。

```powershell
# 直近30分の tree
python "$env:USERPROFILE\.codex\metrics\timeline.py" --minutes 30 --show-tokens

# session をまたぐ開始時刻順 event
python "$env:USERPROFILE\.codex\metrics\timeline.py" --view events --minutes 30
```

## live rollout

`--live` は `routing-metrics.jsonl` を待たず、書き込み中を含む `sessions\**\rollout-*.jsonl` を直接解析します。`collect.py` の cache・出力を変更せず、同じ Session → Turn → Agent tree を構築します。

```powershell
# 直近30分の live tree
python "$env:USERPROFILE\.codex\metrics\timeline.py" --live --minutes 30 --show-tokens

# live rollout を開始時刻順の JSON で取得
python "$env:USERPROFILE\.codex\metrics\timeline.py" --live --view events --minutes 30 --json
```

`--minutes` / `--since` / `--session` 指定時は、まず全 rollout から session ID、親関係、turn の開始・完了時刻だけを軽量に読み、対象 rollout と祖先だけを詳細解析します。表示期間前に始まった Root turn への child 帰属を保ちながら、token/tool 本文の解析量を減らします。短期間・単一 session ほど効果が大きく、無指定の全期間表示は従来同程度です。

親 ID が一意でない場合は session をまたいで推測せず未帰属に隔離します。永続 index や timeline 専用 cache は作らないため、毎回軽量 pass を行います。書き込み中 JSONL の末尾不完全行は無視します。

## options

| option | 内容 |
|---|---|
| `--codex-home PATH` | Codex home。既定は `CODEX_HOME` または `~/.codex` |
| `--input PATH` | Metrics JSONL。既定は `<codex-home>/metrics/routing-metrics.jsonl` |
| `--live` | collect を通さず rollout を直接解析 |
| `--minutes N` | 直近N分（0以上）に絞る |
| `--since ISO-8601` | 指定時刻以降に絞る |
| `--session ID` / `--root-thread ID` | root thread/session ID に絞る（同義） |
| `--view sessions|events` | tree または event 時系列。既定は `sessions` |
| `--json` | machine-readable JSON |
| `--show-tokens` | Agent/Event ごとの token 内訳 |

`--live` と `--input` は同時指定できません。前者は rollout 直接読込、後者は Metrics JSONL 読込であり、混在を明確にエラーにします。

`root_turn_id` のない child など帰属が曖昧な行は `(unassigned)` と表示し、別 session と親子化しません。live JSON の `rollout_files` は軽量 pass の全ファイル数、`parsed_rollout_files` は祖先を含む詳細解析ファイル数です。人間向け ID は12文字超で末尾8文字に短縮しますが JSON は完全な ID を保持します。人間向け時刻は実行環境のローカル時刻、保存 Metrics と JSON の `timestamp` / `end_timestamp` は UTC の元値を維持します。
