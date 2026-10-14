# PALAZZO DATA — 神奈川県5店舗

スマホ向けのパラッツォ5店舗台データ集計Webアプリです。FastAPIとSQLiteを利用します。

## 対応済み
- 神奈川県パラッツォ5店舗の登録
- 日付・店舗選択、機種別集計、台番号別一覧、末尾番号別集計、取得履歴
- CSVエクスポート、管理者用CSVインポート、取得ログ
- iPhone/AndroidのWebブラウザ対応

## 未検証・制限事項
- **公開データ元からのHTML自動取得は未検証です。** 初期状態では停止しています。
- 自動取得を許可する前に、取得元サイトの利用規約とHTML構造を確認してください。robots.txtを遵守し、アクセス制限の迂回はしません。
- 設定1～6の推定・全台系の判定は未実装です。公開データがない間は空の一覧が表示されます。
- Render無料プラン上のSQLiteデータは永続保存できません。サーバー再起動や再配置により失われる場合があります。継続運用には永続データベースが必要です。
- 初期状態で管理トークンは未設定です。CSV取込・手動収集は管理者トークンが設定されるまで実行できません。
- 本サイトは非公式・独立した個人開発物です。

## ローカル実行
Python 3.10以降

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

http://localhost:8000 を開いてください。

## 公開先の基本設定
- runtime: Python
- build: `pip install -r requirements.txt`
- start: `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1`
- `SOURCE_TERMS_ACCEPTED=0`、`AUTO_COLLECT=0`（初期値）

## 対象店舗
- パラッツォ藤沢南店（100812）
- パラッツォ鶴ヶ峰店（100943）
- パラッツォ鶴ヶ峰スロット館（100944）
- パラッツォ湘南台店（100927）
- パラッツォ秦野渋沢店（100956）

取得元候補: https://daidata.goraggio.com/ 
