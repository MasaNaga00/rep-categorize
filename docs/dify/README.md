# テスト用ペイロード集

Difyワークフローの動作確認用 JSON ファイル。

## 使い方

### Dify管理画面で手動テスト
各JSONの `inputs` フィールドの中身を、Dify ワークフローの「テスト実行」画面で入力。

### curl で API 経由テスト
```bash
curl -X POST 'https://<your-dify-host>/v1/workflows/run' \
  -H 'Authorization: Bearer app-xxx...' \
  -H 'Content-Type: application/json' \
  -d @01_ml_simple.json
```

## ファイル一覧

| ファイル | 用途 | 期待結果 |
|---|---|---|
| 01_ml_simple.json | ML 1件、番号なし | M005 (AF不良) |
| 02_ml_split.json | ML 2件、番号分割 | M005 と M007 (シャッター不具合) |
| 03_ml_environment.json | 水濡れ環境要因あり | environment_factors に water |
| 04_ml_not_reproduced.json | 再現せず | reproduction_status = not_reproduced |
| 05_lens_simple.json | LENS 1件 | L008 (ズーム引掛り/作動不具合) |
| 06_ml_sensor_dust_user.json | センサーゴミ（外側付着） | M013 (センサーゴミ、責任=user_or_unknown) |
| 07_ml_batch_5records.json | 5件バッチ | 5件の分類結果が配列で返る |

## 期待結果との比較

LLMの応答の `outputs.result` をパースし、各ケースが期待通りか確認:

```python
import json
import requests

with open("01_ml_simple.json") as f:
    payload = json.load(f)

resp = requests.post(
    "https://<your-dify-host>/v1/workflows/run",
    headers={"Authorization": "Bearer app-xxx...", "Content-Type": "application/json"},
    json=payload,
)
result_str = resp.json()["data"]["outputs"]["result"]
result = json.loads(result_str)

for r in result:
    print(f"  {r['repair_id']}: user={r['user_perspective']['failure_category_code']}, "
          f"repair={r['repair_perspective']['failure_category_code']}")
```
