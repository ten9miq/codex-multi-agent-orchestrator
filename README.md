# codex-multi-agent-orchestrator

Codex Multi-Agent V2 の公開可能な設定、agent role、Metricsを管理するリポジトリです。

## 安全な設定分離

公開リポジトリには端末固有の `config.toml` を保存しません。利用時は
`config.example.toml` をテンプレートとして `CODEX_HOME/config.toml` に適用します。

```powershell
.
\scripts\install.ps1
```

既存の `config.toml` は日時付きバックアップを作成してから置き換えます。
個人パス、MCP接続、通知コマンド、trusted project、session、認証情報は公開設定から除外しています。

## 含まれるもの

- `agents/`: Scout、Worker、Controller、Expertのrole設定
- `metrics/`: rollout解析、smoke test、context/返却結果サイズ観測
- `config.example.toml`: 端末非依存の公開テンプレート

`model_auto_compact_token_limit` は意図的に設定していません。Rootと子agentの実測後に判断します。
