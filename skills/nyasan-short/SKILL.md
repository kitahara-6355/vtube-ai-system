---
name: nyasan-short
description: 「DX事務所のにゃーさん」のYouTubeショートを1本作る（ネタ選び→台本→素材チェック→不足画像の生成指示書→動画書き出し→投稿用テキスト）。「にゃーさんのショートを作って」「今日の1本」で使う。
---

# にゃーさんショートを1本作る

定額の範囲だけで、毎日1本のショートを作るための手順。外部APIは使わない。

## 前提
- 台帳: `characters/characters.json`（キャラ設定）、`assets/library.json`（画像の台帳）
- 画像の正本は共有ドライブ「美麗書院_共有データ」。ローカルの `assets/img/<素材ID>.png` に置いて使う（Drive for desktop の同期フォルダからコピー、または Google Drive コネクタでダウンロード）。
- 必要なもの: Python 3、Pillow、numpy、ffmpeg

## 手順

1. **ネタを決める** — `episodes/` の既存回と重ならないネタを選ぶ。ネタ帳は Drive の「クリエイティブ資産.JSON」の storyIdeas。迷ったら成長段階（よちよち編→つかまり立ち編→ひとり立ち編）の順に進める。
2. **台本を書く** — `nyasan-script` スキルに従って `episodes/EPxxx_<slug>.json` を作る。
3. **素材をチェックする** —
   ```
   python tools/nyasan.py check episodes/EPxxx_<slug>.json
   ```
   - 全シーン `OK` なら 5 へ。
   - `代役` があれば `out/EPxxx/画像の生成指示書.md` ができる。
4. **足りない画像を作る（人の作業）** — `nyasan-image-request` スキルに従う。作らずに進める場合は、台本の表情を手持ちの素材に合わせて書き換えるのが先。
5. **動かすシーンを決める（任意）** — 見せ場の1〜2シーンだけ、台本に `motion`（英語の動きの説明）を書く。最初と最後のコマを変えたいときは `startExpression`/`startPose` と `endExpression`/`endPose` も書く。
   ```
   python tools/nyasan.py storyboard episodes/EPxxx_<slug>.json
   ```
   `out/EPxxx/storyboard/` に最初と最後のコマ（9:16、字幕なし）と `絵コンテ指示書.md` ができる。Flow の「Frames to Video」（Veo 3.1 Fast、9:16、8秒）にコマを入れて生成し（人の作業）、`assets/clips/EPxxx/sceneNN.mp4` に置く。1本あたり2クリップ＝40クレジットが上限の目安（Business Standard の毎日50に収まる）。クリップがないシーンは静止画で書き出されるので、生成が間に合わなくても投稿は止まらない。
   Flow には指示書の「依頼文」をそのまま送る（最初のコマ→最後のコマの順に添付、音声なし）。
   生成後は `python tools/nyasan.py review episodes/EPxxx_<slug>.json` で `out/EPxxx/review/sceneNN_確認シート.jpg` を作り、**人が見て採用を決める**。良ければ台本のシーンに `"clipApproved": true`、一部だけ使うなら `"clipRange": [開始秒, 終了秒]` を書く。作り直しは1シーン2回まで、ダメなら静止画のまま出す。動きが速すぎるときは `seconds` でシーンを延ばす（最大2倍速まで縮める仕組み）。
6. **書き出す** —
   ```
   python tools/nyasan.py render episodes/EPxxx_<slug>.json
   ```
   `out/EPxxx/` に `EPxxx_short.mp4`、`preview_first.jpg`、`投稿用テキスト.txt` ができる。
7. **確認する** — `preview_first.jpg` と動画を見る。字幕の折り返しが変なら台本の `line` に `\n` を入れて直し、6 をやり直す。右上に「代役」「未承認」の札が出ている動画は投稿しない。
8. **投稿する（人の作業）** — YouTube Studio に弊社 Business Standard アカウントでログインし、`投稿用テキスト.txt` を見ながら予約投稿する。X は手動投稿（X API は使わない方針）。
9. **記録する** — 投稿日と URL を `episodes/EPxxx_<slug>.json` の `published` に書く。

## 動く漫画で作る（2026-10-28 からの標準）

北原さんの方針: 漫画のコマ送りで完成させ、そこから1コマずつアニメに近づける。詳しくは提案書 https://claude.ai/artifact/KEoJGXdf8b6kHw7NLsGtLj 。

1. 台本の各シーンに `panels`（1コマずつ）を書く。1シーン＝漫画1ページ、2〜3コマ。コマには `asset`（素材ID、`@screen` で描いた画面）、`crop`（full/bust/face か [x0,y0,x1,y1]）、`text`（縦書きの吹き出し）、`sfx`（擬音）、`fx`（lines 集中線 / flash / shake / shout 叫び / tone / light）、`seconds`、`h`/`row`/`w`（コマ割り）を書く。
2. 足りない絵は `need`（`id`・`character`・`refs`・`desc`）を書く。絵ができるまでは `asset` が「代役」として入る。
3. 動かすコマは `flow`（`from` 前の絵、`motion` 英語の動き）を書く。1コマ1動作、前後の絵の差は小さく。
4. 実行する。
   ```
   python tools/manga.py sheet  episodes/EPxxx_<slug>.json   # コマ一覧（北原さんの確認用）
   python tools/manga.py prep   episodes/EPxxx_<slug>.json   # 画像の生成指示書と Flow のコマ画像・依頼文
   python tools/manga.py render episodes/EPxxx_<slug>.json   # 動く漫画の mp4
   ```
5. 生成された絵を `assets/img/<need の id>.png` に置くと自動で差し替わる。Flow の動画は `assets/clips/EPxxx/<コマ番号>.mp4` に置き、北原さんが確認シートを見て採用したら `flow` に `"approved": true` を書く。

## やらないこと
- Vertex AI、Gemini API の画像・動画モデル、GCP 上の Claude など従量課金のサービスを呼ぶこと。
- キャラ画像を公開リポジトリにコミットすること（`assets/img/` は .gitignore 済み）。
- 鍵ファイル（サービスアカウント JSON 等）を作ること。
