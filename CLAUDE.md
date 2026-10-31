# CLAUDE.md

にゃーさんショートの制作リポジトリ。手順は `skills/nyasan-short/SKILL.md` が正。

- Google は API 従量課金を使わない（Vertex AI、Gemini API の画像・動画モデル、Cloud Run 等を提案しない）。画像生成は定額の Gemini アプリで人が行う。
- 公開リポジトリなので、キャラ画像・生成物・鍵ファイルはコミットしない（`assets/img/`、`out/` は .gitignore）。
- 文言は日本語。チャンネルの芯は `skills/nyasan-channel-core/SKILL.md`（言葉の正本は `characters/characters.json`）。台本・画像・動画を作ったら `python tools/policy.py episodes/<回>.json` の方針チェックを通し、目で見る項目も確かめる。
- 変更したら `python -m pytest -q` と `python tools/nyasan.py check episodes/EP001_mouse.json` を通す。
