# テニスコート予約カレンダー

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
