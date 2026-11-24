# Oshigoe への貢献

不具合の報告、機能の提案、コードやドキュメントの修正を受け付けます。AI の支援を受けた貢献も提出できます。

## 作業前に確認する

1. 既存の Issue やプルリクエスト（PR）を確認します。同じ内容があれば、必要な情報を既存の議論に追加してください。
2. 不具合を報告する場合は、発生した OS とブラウザー、再現手順、期待する動作、実際の動作を記載します。
3. 大規模な機能追加や設計変更では、実装前に Issue で目的と方針を相談してください。

## 変更を提出する

1. リポジトリをフォークし、作業ブランチで変更します。1 件の PR は 1 つの目的に絞ってください。コミットメッセージは [AGENTS.md の規約](AGENTS.md#コミットメッセージ) に従います。
2. 変更に関連するテストを実行します。Python 3.12 の仮想環境に [requirements.txt](requirements.txt) の依存パッケージを導入し、Node.js を利用できる状態で、リポジトリのルートから実行してください。

   ```sh
   python -m unittest
   node --test "tests/frontend/**/test_*.mjs"
   ```

   FFmpeg を使用するテストは、PATH 上の FFmpeg か、起動スクリプトが `.runtime` に配置した FFmpeg を使用します。どちらもない場合、そのテストはスキップされます。

3. PR を提出し、変更理由、確認手順と結果、未確認の項目を記載します。画面や再生動作を変更した場合は、確認に使用した OS とブラウザーも記載してください。

## AI の支援を受けた場合

- 投稿者本人が差分全体を確認し、変更の理由を説明できる状態で提出してください。レビューへの回答も投稿者が担当します。
- PR 本文に AI の用途と対象を簡潔に記載してください。たとえば「AI を使用して、入力 URL のテストの下書きを作成しました。」と記載します。
- PR、Issue、コメントは内容を確認したうえで投稿してください。エージェントを使用する場合も、投稿する内容と操作を本人が承認してください。

### pstack を導入する

AI の支援を受けて変更する場合は、使用するツールに対応する pstack プラグインの導入を推奨します。
導入と初回設定を済ませたら、新しいセッションを開始してください。

#### Cursor

Cursor のチャットで `/add-plugin pstack` を実行して純正の pstack を導入します。初回は `/setup-pstack` でモデルを設定し、作業時は `/poteto-mode` を使用してください。 [純正 pstack の導入手順](https://github.com/cursor/plugins/tree/main/pstack)

#### Codex

ターミナルで次のコマンドを実行します。

```sh
codex plugin marketplace add michael-denyer/pstack-claude
codex plugin add pstack@pstack-claude
```

Codex の `/hooks` で `Loading pstack routing` を確認して信頼してください。 [pstack-claude の Codex 向け手順](https://github.com/michael-denyer/pstack-claude/blob/main/docs/reference.md#codex)

#### Claude Code

Claude Code のチャットで次のコマンドを実行します。 [pstack-claude の導入手順](https://github.com/michael-denyer/pstack-claude#install)

```text
/plugin marketplace add michael-denyer/pstack-claude
/plugin install pstack@pstack-claude
```

### 日本語の推敲スキルを導入する

yomiyasu での推敲は、AI が生成した日本語の文章に限ります。Node.js 22.20 以降と Python 3 を利用できる環境で、リポジトリのルートから次のコマンドを実行してください。

```sh
npx --yes skills experimental_sync --yes --no-frozen-lockfile --agent cursor codex claude-code
```

[package.json](package.json) の `skills` に指定した配布元の既定ブランチから yomiyasu を取得し、Cursor と Codex 用の `.agents/skills/`、Claude Code 用の `.claude/skills/` に配置します。導入後はこのプロジェクトでエージェントの新しいセッションを開始してください。

導入済みの yomiyasu を更新するには、`npx --yes skills update yomiyasu --project --yes` を実行します。

## ライセンス

提出する変更を [Apache-2.0](LICENSE) の条件で貢献できることを確認してください。AI を使用した場合は、そのツールの利用条件も確認します。

第三者のコードや素材を含める場合は、出典とライセンスを PR に記載してください。利用条件に従い、必要な著作権表示、ライセンス文書、通知をリポジトリや配布物に収録してください。依存物を追加した場合は [サードパーティーのライセンス一覧](THIRD_PARTY_NOTICES.md) も更新します。
