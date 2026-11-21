# ジャービス開発用メモ

> このファイルは、ジャービスを**開発する**ときの決まりごと。
> ジャービス本体（workspace フォルダから動いている Claude）として読んでいる場合は、このファイルは無視し、`workspace/CLAUDE.md` に従うこと。

## 着手する前に
- まず `README.md` と `config.py` を読んで、今の構成と設定を確認する
- 設定値は `config.py` に集める。ほかのファイルに直接書き込まない

## 触ってはいけないもの
- `workspace/CLAUDE.md` はジャービスの人格と記憶。ジャービスと記憶係（`memory.py`）が書き足しているので、頼まれない限り書き換えない
- `workspace/.claude/settings.json` は起動のたびに `brain.py` が書き直すので、直接編集しても消える。変えたいときは `brain.py` を直す
- `guard.py` の確認の仕組み（削除・送信などを止めて、声で承認されたときだけ通す）を弱める変更は、必ず UI 上で確認を取ってから行う

## 置き場所
- venv・モデル・セッション情報は `%LOCALAPPDATA%\jarvis` に置く（OneDrive で同期させない）
- Python は `%LOCALAPPDATA%\jarvis\.venv\Scripts\python.exe` を使う
- 動作の記録は `logs/` にある。不具合を調べるときは最初にここを見る

## 変更を反映する
- 再起動は `New-Item "$env:LOCALAPPDATA\jarvis\restart.flag" -Force` で合図を置くだけ（約 10 秒で本体が起動し直す。プロセスは止めない）
- リモコン（remote.py）は `New-Item "$env:LOCALAPPDATA\jarvis\remote_restart.flag" -Force`（返事を返し終えてから約 5 秒で起動し直す）
- 動いていなければ `ジャービス.lnk` を `Start-Process` で起動する
- 再起動は、その会話で最初の 1 回だけ「再起動します」と確認し、以後は確認なしで行ってよい

## 作るときの方針
- 返事の速さ（話し終えてから 2〜4 秒）を落とさない。重い処理を足すときは、どのくらい遅くなるかを測って伝える
- 無料で PC の中で動くものを優先する（Claude の利用枠を節約するため）
- 機能を足したり変えたりしたら `README.md` も更新する
- ユーザーの呼び名は「ユーザー」
