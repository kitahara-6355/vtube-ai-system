# CLAUDE.md

にゃーさんショートの制作リポジトリ。手順は `skills/nyasan-short/SKILL.md` が正。

- Google は API 従量課金を使わない（Vertex AI、Gemini API の画像・動画モデル、Cloud Run 等を提案しない）。画像生成は定額の Gemini アプリで人が行う。
- 公開リポジトリなので、キャラ画像・生成物・鍵ファイルはコミットしない（`assets/img/`、`out/` は .gitignore）。
- 文言は日本語。台本の芯は `characters/characters.json` の `channel.principles`。
- 変更したら `python -m pytest -q` と `python tools/nyasan.py check episodes/EP001_mouse.json` を通す。
