# チャット2: Dify実行チャット引き継ぎ資料

> このチャットの役割: チャット1が生成した records.json を Dify に投げて、LLM分類結果 classifications.json を取得します。
> **先に `00_common.md` を確認してください**。共通基盤情報はそちらに記載されています。

---

## このチャットの役割

```
チャット1から受領
    ↓
[records.json読込] → [Dify Workflow API呼出] → [LLM応答パース] → [JSON保存]
    ↓
チャット3へ引き継ぎ
```

### このチャットでやること

- チャット1が生成した records.json を読み込む
- Dify ワークフロー実行 API を呼び出す (バッチ処理)
- LLM の応答 JSON をパース・検証
- classifications.json として保存
- 失敗バッチの再実行
- Dify ワークフローの構築 (まだの場合)

### このチャットでやらないこと

- ❌ split処理 (チャット1の責務、固定済み)
- ❌ プロンプト内容の変更 (Difyワークフロー側で固定済み)
- ❌ コード体系の変更 (classification_codes.yaml)
- ❌ 派生指標の計算・最終CSV出力 (チャット3の責務)

---

## 入力契約 (チャット1からの受領)

### 入力ファイル

```
outputs/prepared_data/{timestamp}_records.json   # ★主入力
outputs/prepared_data/{timestamp}_meta.json      # 参考
outputs/prepared_data/{timestamp}_split_full.parquet  # 参考、デバッグ用
```

### records.json のスキーマ

JSON配列、各要素は以下のキーを持つ (チャット1の出力契約):

```json
[
  {
    "repair_id": "R016",
    "sub_id": 1,
    "product_type": "ML",
    "user_text": "レンズ接点",
    "user_context": "",
    "repair_text": "レンズ側にて対応します。カメラには異常なし",
    "repair_context": "",
    "internal_1": "内進行",
    "internal_2": "レンズ側の対応で解決"
  }
]
```

このスキーマは **既に `dify_client.py` が想定する形式と一致** している。変換不要。

---

## 出力契約 (チャット3への引き継ぎ)

### 出力ファイル

```
outputs/dify_results/
├── {timestamp}_classifications.json     # ★主成果物
├── {timestamp}_classifications.parquet  # 同上、Parquet形式
├── {timestamp}_failed_records.json      # 失敗バッチの入力レコード (再実行用)
└── {timestamp}_run_meta.json            # 実行メタ情報
```

**重要**: `{timestamp}` はチャット1の入力ファイルと同じ値を使う。これにより、チャット3で結合時に対応が分かりやすい。

### classifications.json のスキーマ (厳密)

JSON配列、各要素は以下の構造:

```json
[
  {
    "repair_id": "R016",
    "sub_id": 1,
    "user_perspective": {
      "failure_category_code": "M020",
      "confidence": 0.85,
      "evidence": "ユーザ「レンズ接点」より",
      "insufficient_info": false
    },
    "repair_perspective": {
      "failure_category_code": "M020",
      "confidence": 0.95,
      "evidence": "修理者「レンズ側にて対応」より",
      "insufficient_info": false
    },
    "reproduction_status": "reproduced",
    "reproduction_evidence": "...",
    "reproduction_confidence": 0.7,
    "environment_factors": ["unknown"],
    "environment_evidence_source": {"unknown": "user"},
    "environment_evidence": {"unknown": "環境記述なし"},
    "environment_confidence": {"unknown": 1.0}
  }
]
```

### run_meta.json のスキーマ

```json
{
  "timestamp": "20260506_143012",
  "input_records_path": "outputs/prepared_data/20260506_143012_records.json",
  "input_records_count": 7,
  "output_classifications_count": 7,
  "batch_size": 10,
  "total_batches": 1,
  "successful_batches": 1,
  "failed_batches": 0,
  "total_tokens": 8523,
  "elapsed_seconds": 12.5,
  "failed_batch_indices": [],
  "validation_issues": []
}
```

### failed_records.json

失敗したバッチの入力レコードを集約。再実行できる形式。

```json
[
  // 失敗バッチに含まれていたレコード (records.json と同じスキーマ)
]
```

成功時は空配列 `[]`。

---

## 完成済みコード (このチャットでは触らない)

| ファイル | 状態 | 役割 |
|---|---|---|
| `src/dify_client.py` | ✅ 完成 | Dify API クライアント、同期/非同期対応 |
| `examples/dify_client_demo.py` | ✅ 完成 | 使い方のデモ |
| `examples/dify_curl_test.sh` | ✅ 完成 | curlでの疎通確認 |
| `docs/dify/dify_workflow_setup.md` | ✅ 完成 | Difyワークフロー構築手順書 |
| `docs/dify/test_payloads/` | ✅ 完成 | テスト用JSONペイロード集 |

### dify_client.py の使い方 (再確認)

```python
from dify_client import (
    DifyClient,
    flatten_results,
    collect_failed_records,
)

# 環境変数から自動取得
client = DifyClient(timeout_seconds=120, max_retries=3)

# 同期API (Notebook向け)
results, report = client.run_batches(
    records, product_type="ML", batch_size=10,
)
print(report.summary())

# 結果フラット化
classifications = flatten_results(results)

# 失敗レコード抽出 (再実行用)
failed = collect_failed_records(results)

# 非同期API (プロダクション向け)
import asyncio
results, report = asyncio.run(
    client.run_batches_async(
        records, product_type="ML",
        batch_size=10, max_concurrent=5,
    )
)
```

---

## このチャットの残タスク

### 優先度高

- [ ] **Difyワークフローの構築 (まだなら)**
  - `docs/dify/dify_workflow_setup.md` の手順に従う
  - `outputs/dify_prompts/system_prompt_ML.txt` 等を貼り付け
  - 管理画面でテスト実行 (`docs/dify/test_payloads/01_ml_simple.json` 等)

- [ ] **APIキーの取得と .env 設定**
  ```
  DIFY_BASE_URL=https://your-dify-host
  DIFY_API_KEY=app-xxxxx
  ```

- [ ] **疎通確認**
  ```bash
  ./examples/dify_curl_test.sh 01_ml_simple
  ```
  または `02_pilot_run.ipynb` のセクション5まで実行

- [ ] **本処理用Notebookの作成 (or 02_pilot_run.ipynb の拡張)**
  - records.json 読み込み → Dify実行 → classifications.json 保存
  - 製品種別ごとに処理 (ML/LENSが混在する場合)
  - エラー時は failed_records.json として保存

  実装イメージ:
  ```python
  import json
  from datetime import datetime
  from pathlib import Path

  # 入力読み込み
  TIMESTAMP = "20260506_143012"  # チャット1から受領した値
  records_path = PROJECT_ROOT / "outputs" / "prepared_data" / f"{TIMESTAMP}_records.json"
  records = json.loads(records_path.read_text(encoding="utf-8"))

  # 製品種別ごとに分割 (ML/LENS混在対応)
  by_product = {}
  for r in records:
      by_product.setdefault(r["product_type"], []).append(r)

  # 各製品種別を実行
  all_classifications = []
  all_failed = []
  for product_type, recs in by_product.items():
      results, report = client.run_batches(
          recs, product_type=product_type, batch_size=10,
      )
      all_classifications.extend(flatten_results(results))
      all_failed.extend(collect_failed_records(results))

  # 出力
  output_dir = PROJECT_ROOT / "outputs" / "dify_results"
  output_dir.mkdir(parents=True, exist_ok=True)

  classifications_path = output_dir / f"{TIMESTAMP}_classifications.json"
  classifications_path.write_text(
      json.dumps(all_classifications, ensure_ascii=False, indent=2),
      encoding="utf-8",
  )

  # parquet も保存
  import pandas as pd
  pd.DataFrame(all_classifications).to_parquet(
      output_dir / f"{TIMESTAMP}_classifications.parquet"
  )

  # 失敗レコード
  failed_path = output_dir / f"{TIMESTAMP}_failed_records.json"
  failed_path.write_text(
      json.dumps(all_failed, ensure_ascii=False, indent=2),
      encoding="utf-8",
  )

  # メタ情報
  meta = {
      "timestamp": TIMESTAMP,
      "input_records_path": str(records_path),
      "input_records_count": len(records),
      "output_classifications_count": len(all_classifications),
      ...
  }
  ```

### 優先度中

- [ ] **LLM応答の品質確認**
  - validation_issues に何が記録されているか確認
  - コードが体系内か、reproduction_status が4種類のいずれかか等

- [ ] **失敗バッチの再実行ロジック**
  - failed_records.json があれば自動再実行 (オプション機能)

- [ ] **コスト・パフォーマンス測定**
  - 1バッチあたりのトークン数、所要時間を記録
  - 大量実行時の見積もり計算

### 優先度低

- [ ] バッチサイズの自動チューニング (応答品質に応じて動的調整)
- [ ] streaming モードの検討 (現状は blocking 固定)

---

## 関連ファイル一覧

### このチャットで参照・編集するファイル

```
# 主に参照
docs/dify/dify_workflow_setup.md
src/dify_client.py
examples/dify_client_demo.py

# 編集する可能性
notebooks/02_pilot_run.ipynb (or 新規Notebook)
.env (機密情報)
docs/dify/test_payloads/  (テストケース追加時)

# 入力 (チャット1から受領)
outputs/prepared_data/{timestamp}_records.json

# 出力 (チャット3へ引き継ぎ)
outputs/dify_results/{timestamp}_classifications.json
outputs/dify_results/{timestamp}_classifications.parquet
outputs/dify_results/{timestamp}_failed_records.json
outputs/dify_results/{timestamp}_run_meta.json
```

### このチャットでは触らないファイル

```
src/split.py              # チャット1の領域 (固定済み)
src/zone_extractor.py     # チャット1の領域 (固定済み)
src/derive_metrics.py     # チャット3の領域 (未実装)
src/output_formatter.py   # チャット3の領域 (未実装)
config/classification_codes.yaml  # 全チャット共通、変更しない
config/prompts/           # Dify貼り付け済み、変更時は再生成必要
```

---

## トラブルシューティング (実機で起きやすい問題)

### 1. JSON Mode で配列が返らない

OpenAI の JSON Mode は **オブジェクト**しか保証しない仕様。LLMが `{"results": [...]}` のように包んで返す可能性がある。

**`dify_client.py` は自動対応済み**:
```python
# _parse_llm_classifications() 内で自動的に
# 配列 → そのまま使用
# {"results": [...]} → results を取り出す
# {"data": [...]}, {"classifications": [...]}, {"records": [...]} も対応
```

それでもダメなら、Difyのシステムプロンプトを以下のように修正:

```
出力フォーマット:
{
  "results": [
    {...},
    ...
  ]
}
```

### 2. タイムアウト

バッチサイズが大きいと120秒で足りないことがある。

**対処**:
- `DifyClient(timeout_seconds=180)` に増やす
- バッチサイズを下げる (10 → 5)

### 3. レスポンスの件数不一致

入力10件に対し出力9件、等。

**原因**: max_tokens 不足 or LLMがスキップ

**対処**:
- Difyの LLMノードで max_tokens を増やす (8000 → 12000)
- バッチサイズを下げる

### 4. 認証エラー (401/403)

**対処**:
- APIキー再生成 (Dify管理画面 → APIアクセス)
- `app-xxxxx` 形式であることを確認 (OpenAIの `sk-xxxxx` ではない)
- セルフホスティングのbase_urlに `/v1/workflows/run` を含めない (`/v1` 含めるかどうかで違う場合あり、要検証)

---

## デバッグのコツ

### Dify管理画面のログ確認

API呼び出しが失敗したら、Dify管理画面の「ログ」タブで実際のLLM応答を確認できる。プロンプトに問題があるか、LLM応答が想定外かを切り分ける。

### 単体実行での検証

```python
# 1件だけ送って動作確認
client = DifyClient()
classifications, metadata = client.run_workflow(
    records[:1], product_type="ML",
)
print(json.dumps(classifications[0], ensure_ascii=False, indent=2))
```

### モックテストの活用

実APIを叩かなくても、`tests/test_dify_client.py` の34件は requests_mock でモック実行できる。コード変更時の回帰確認に使う:

```bash
python3 -m pytest tests/test_dify_client.py -v
```

---

## 引き継ぎ時の確認事項

新チャット開始時、以下を確認してください:

- [ ] `00_common.md` を読んで全体像を把握
- [ ] チャット1から `{timestamp}_records.json` を受領 (パスを共有してもらう)
- [ ] `pytest tests/test_dify_client.py` で34件パス確認
- [ ] `.env` に DIFY_BASE_URL と DIFY_API_KEY が設定されている
- [ ] Difyワークフローが構築・公開済み (or 構築する)
- [ ] curl で疎通確認 (`./examples/dify_curl_test.sh 01_ml_simple`)

---

## チャット3への引き継ぎ完了条件

このチャットの作業が完了したと言えるのは:

- [ ] `outputs/dify_results/{timestamp}_classifications.json` が生成されている
- [ ] スキーマが上記契約と一致している (各要素が user_perspective, repair_perspective, reproduction_status, environment_factors を持つ)
- [ ] failed_records.json が空 or 内容を確認済み
- [ ] run_meta.json で総トークン数・所要時間が記録されている
- [ ] チャット1の records.json と件数が一致している (失敗以外)

これらが揃ったら、`{timestamp}` をチャット3に伝えて引き継ぎ完了。
