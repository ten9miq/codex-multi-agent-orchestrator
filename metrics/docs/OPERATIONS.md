# 運用とトラブルシューティング

## 推奨運用

設定変更直後は次の順に実施します。

```text
Smoke Test
↓
smoke.py
↓
raw rollout と実効設定を確認
```

通常運用では次を繰り返します。

```text
collect.py
↓
report.py
↓
人間が Routing 品質 / コストを確認
↓
必要に応じて AGENTS.md や agent 設定を調整
```

初期版は Metrics から設定を自動変更しません。改修時は変更前後、変更理由、token cost・完遂率への影響、rollback 条件、Smoke Test、Metrics での評価方法を残します。

## トラブルシューティング

### rollout が見つからない

`...以降のrolloutが見つかりません` は対象時間が狭い場合があります。時間を広げます。

```powershell
python "$env:USERPROFILE\.codex\metrics\smoke.py" --minutes 30 --table
```

### Codex が書き込み中

対応済みです。Windows では共有読み取りで JSONL を開くため、Codex が session を保持中でも読めます。書き込み途中の最終 JSON 行は無視し、次回実行で再取得します。

### `turn_context` がない

`! turn_context が見つかりません` は model / effort / V1/V2 を実ログから確認できない状態です。`smoke.py` は警告し、PASS にしません。

### Smoke Test が複数 Root を拾う

`--expect` は選択期間に Root が1つであることが前提です。実行直前に `$SmokeStart = Get-Date` を保存し、`--since $SmokeStart.ToString("o")` を使うのが確実です。

## よく使うコマンド

```powershell
# 直近10分の配線
python "$env:USERPROFILE\.codex\metrics\smoke.py"

# 時刻以降の厳密な nested 配線検証
python "$env:USERPROFILE\.codex\metrics\smoke.py" `
  --since $SmokeStart.ToString("o") `
  --expect controller_sol_scout --table

# Metrics収集、30日・7日レポート
python "$env:USERPROFILE\.codex\metrics\collect.py"
python "$env:USERPROFILE\.codex\metrics\report.py"
python "$env:USERPROFILE\.codex\metrics\report.py" --days 7
```
