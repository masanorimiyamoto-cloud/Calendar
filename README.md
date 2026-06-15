# 千葉北テニス

Flask で作ったテニスサークル向けのコート予約・出欠管理アプリです。

## 主な機能

- 月間カレンダーで予約日を確認
- 日付ごとにコート予約を登録
- 固定メンバーの名簿を管理
- 各予約でメンバーごとに「参加」「不参加」「未定」を切り替え
- 参加人数は最大 4 名まで
- 日本の祝日をカレンダーに表示

## ローカル起動

```powershell
.\.python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

起動後、ブラウザで `http://localhost:10000` を開きます。

## Vercel へのデプロイ

`vercel.json` により、すべてのリクエストは `api/index.py` にルーティングされます。
本番環境では `DATABASE_URL` に PostgreSQL の接続文字列を設定してください。

本番でテーブル作成やカラム追加を実行したい時だけ、環境変数 `INIT_DB=1` を一時的に設定して起動してください。
通常運用時は `INIT_DB` を未設定にして、起動時のDB初期化をスキップします。
新しいDBカラムを追加したリリース後は、一度だけ `INIT_DB=1` で再デプロイしてから、`INIT_DB` を外して通常運用に戻してください。

合言葉ログインには、環境変数 `ACCESS_CODE` を設定してください。
また、セッション署名用に `SECRET_KEY` も本番用のランダムな文字列に変更してください。
