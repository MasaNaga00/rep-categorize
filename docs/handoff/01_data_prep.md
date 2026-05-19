# チャット1: データ準備チャット引き継ぎ資料

> このチャットの役割: CSV読み込みから Dify 送信用 records.json 生成までを担当します。
> **先に `00_common.md` を確認してください**。共通基盤情報はそちらに記載されています。

---

## このチャットの役割

```
[CSV] → [カラムマッピング] → [split.py: 分割] → [JSON出力]
                                                   ↓
                                          チャット2へ引き継ぎ
```

### このチャットでやること

- 実データCSVを読み込む
- ColumnMappingを設定して `split.py` で分割処理
- 分割結果を検証(目視・パターン別集計)
- Dify送信用の `records.json` を生成して保存
- 件数を増やしての本格的な前処理

### このチャットでやらないこと

- ❌ Dify API への送信 (チャット2の責務)
- ❌ LLM応答のパース (チャット2の責務)
- ❌ 派生指標の計算 (チャット3の責務)
- ❌ Tableau用CSV出力 (チャット3の責務)

---

## 入力契約

### 入力データの仕様

CSVファイル (`data/test_data.csv` 等) を入力とする。

**実データCSVのカラム例 (実機運用)**:

| CSVカラム名 | 用途 | split.py のColumnMapping |
|---|---|---|
| `ID` | 修理ID | `repair_id="ID"` |
| `事業コード` | 製品種別 (ML/LENS) | `product_type="事業コード"` |
| `user_comment` | ユーザコメント | `user_comment="user_comment"` |
| `repair_comment` | 修理者コメント | `repair_comment="repair_comment"` |
| `Internal_1` | 内部コメント1 | `internal_1="Internal_1"` |
| `Internal_2` | 内部コメント2 | `internal_2="Internal_2"` |
| その他 (`date`, `Develop`, `Product_name`, `Serial`, `Garantee`) | 使わない | - |

**注**: 本パイプラインでは `事業コード` で ML/LENS を判定する。SQL段階で確定済みの値を信用する。

### CSV例 (1件)

```csv
date,事業コード,Develop,Product_name,Serial,Garantee,user_comment,repair_comment,Internal_1,Internal_2,ID
3/3,ML,S250,Alpha 7,4311111100,保証内,①レンズ接点 ②電源 ③シャッター ④LCD ⑤画像 ⑥音声 ⑦動画,①②レンズ側にて対応します。カメラには異常なし ③④⑤⑥⑦ご指摘外の現象,内進行,レンズ側の対応で解決,R016
```

このR016ケースは split.py で **7分割** される (連続マーカー対応のテストケース)。

---

## 出力契約 (チャット2への引き継ぎ)

### 出力ファイル

```
outputs/prepared_data/
├── {timestamp}_records.json         # ★主成果物。Dify送信用
├── {timestamp}_split_full.parquet   # 全カラム保持の参考データ
└── {timestamp}_meta.json            # split統計、警告ログ
```

### records.json のスキーマ (厳密)

**JSON配列のlist[dict]形式**。各要素は以下のキーを必ず持つ:

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

**型と制約**:

| キー | 型 | 制約 |
|---|---|---|
| `repair_id` | string | 必須、空文字不可 |
| `sub_id` | integer | 必須、1始まり |
| `product_type` | string | "ML" or "LENS" |
| `user_text` | string | 空文字可 |
| `user_context` | string | 空文字可 |
| `repair_text` | string | 空文字可 |
| `repair_context` | string | 空文字可 |
| `internal_1` | string | 空文字可 |
| `internal_2` | string | 空文字可 |

### split_full.parquet のカラム

`split.py` の出力カラム全て (12カラム) を保持。後段でデバッグや postamble 確認に使う。

```python
# split.py の OUTPUT_COLUMNS
OUTPUT_COLUMNS = [
    "repair_id", "sub_id", "product_type",
    "user_text", "user_context", "user_postamble",
    "repair_text", "repair_context", "repair_postamble",
    "internal_1", "internal_2", "split_info",
]
```

### meta.json のスキーマ

```json
{
  "timestamp": "20260506_143012",
  "csv_path": "data/test_data.csv",
  "csv_records_in": 1,
  "csv_records_used": 1,
  "split_records_out": 7,
  "split_stats": {
    "no_split_count": 0,
    "split_count": 1,
    "marker_mismatch_count": 0,
    "duplicate_marker_count": 0,
    "fallback_detected_count": 0,
    "abnormal_split_count": 0,
    "empty_record_count": 0
  },
  "warnings": []
}
```

---

## 完成済みコード (このチャットでは触らない)

| ファイル | 状態 | 役割 |
|---|---|---|
| `src/codes_loader.py` | ✅ 完成 | YAML読み込み (チャット1では使わないが import 可) |
| `src/zone_extractor.py` | ✅ 完成 | 4ゾーン分解、マーカーグループ化 |
| `src/split.py` | ✅ 完成 | ①②③分割、ColumnMapping対応 |
| `src/prompt_builder.py` | ✅ 完成 | Dify貼り付け用テキスト生成 (チャット1ではほぼ参照のみ) |

### split.py の使い方 (再確認)

```python
from split import ColumnMapping, SplitConfig, split_records

# CSVカラム名のマッピング
column_mapping = ColumnMapping(
    repair_id="ID",
    product_type="事業コード",
    user_comment="user_comment",
    repair_comment="repair_comment",
    internal_1="Internal_1",
    internal_2="Internal_2",
)

config = SplitConfig(columns=column_mapping)
split_df, report = split_records(input_df, config)

print(report.summary())
```

---

## このチャットの残タスク

### 優先度高

- [ ] **records.json 出力ロジックの実装**
  - 専用Notebook (`notebooks/02_pilot_run.ipynb` 拡張 or 新規作成) 内で、split_df を records.json に変換して保存する処理を追加
  - 既存の Notebook はDify送信まで含むので、**境界Aで止めるバージョン**が欲しい

  実装イメージ:
  ```python
  from datetime import datetime
  from prompt_builder import RECORD_KEY_ORDER
  import json

  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  output_dir = PROJECT_ROOT / "outputs" / "prepared_data"
  output_dir.mkdir(parents=True, exist_ok=True)

  # records.json 生成
  records = [
      {k: row[k] for k in RECORD_KEY_ORDER}
      for _, row in split_df.iterrows()
  ]
  records_path = output_dir / f"{timestamp}_records.json"
  records_path.write_text(
      json.dumps(records, ensure_ascii=False, indent=2),
      encoding="utf-8",
  )

  # split_full.parquet (全カラム保持)
  split_path = output_dir / f"{timestamp}_split_full.parquet"
  split_df.to_parquet(split_path, index=False)

  # meta.json
  meta = {
      "timestamp": timestamp,
      "csv_path": str(CSV_PATH),
      "csv_records_in": len(raw_df),
      "csv_records_used": len(pilot_df),
      "split_records_out": len(split_df),
      "split_stats": {...},
      "warnings": list(report.warnings),
  }
  meta_path = output_dir / f"{timestamp}_meta.json"
  meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
  ```

### 優先度中

- [ ] **実データ件数を増やしての検証**
  - 現状の test_data.csv は1件のみ
  - 数十件〜数百件の本物データで分割パターンの偏りを確認
  - フォールバック検出件数、重複マーカー件数を集計

- [ ] **新しいパターンの発見と対応**
  - 実データで未知の分割パターンが見つかった場合、`zone_extractor.py` への対応
  - 例: 半角番号での運用が多いケース、文中マーカーの異常出現等

### 優先度低

- [ ] split.py のパフォーマンスチューニング (数千件の場合)
- [ ] CSVバリデーション (重複ID検出、データ品質チェック)

---

## 関連ファイル一覧

### このチャットで参照・編集するファイル

```
# 主に参照
config/classification_codes.yaml
src/zone_extractor.py
src/split.py
data/test_data.csv

# 編集する可能性
notebooks/02_pilot_run.ipynb (or 新規Notebook)
tests/conftest.py (新規パターン追加時)
tests/test_split.py
tests/test_zone_extractor.py

# 出力
outputs/prepared_data/{timestamp}_records.json
outputs/prepared_data/{timestamp}_split_full.parquet
outputs/prepared_data/{timestamp}_meta.json
```

### このチャットでは触らないファイル

```
src/dify_client.py        # チャット2の領域
src/derive_metrics.py     # チャット3の領域 (未実装)
src/output_formatter.py   # チャット3の領域 (未実装)
docs/dify/                # Difyワークフロー設定 (チャット2)
config/prompts/           # チャット2のDifyに貼り付け済み
```

---

## デバッグ・検証のコツ

### split.py の動作確認方法

1. **既存テスト実行**:
   ```bash
   python3 -m pytest tests/test_split.py tests/test_zone_extractor.py -v
   ```

2. **既知の困難ケース (R016) で動作確認**:
   - 入力: `①レンズ接点 ②電源 ③シャッター ④LCD ⑤画像 ⑥音声 ⑦動画` / `①②レンズ側にて対応 ③④⑤⑥⑦ご指摘外の現象`
   - 期待: 7分割、sub_id 1,2 が同じ repair_text、sub_id 3-7 が同じ repair_text

3. **01_split_validation.ipynb** で18パターンを検証

### 警告ログの読み方

`split_report.warnings` には以下のような警告が入る:

| 警告タイプ | 意味 | 対処 |
|---|---|---|
| `[fallback_detected]` | 全角丸数字以外の番号表記検出 | 半角番号での運用なら現状の動作で問題なし |
| `[marker_mismatch]` | ユーザと修理者の番号セット不一致 | 元データを確認、人手判断必要 |
| `[duplicate_markers]` | 同じ番号が複数回出現 | 元データを確認、不分割でOK |
| `[abnormal_split]` | 分割数が閾値超過 | 8分割以上は要注意、データ品質確認 |

---

## 引き継ぎ時の確認事項

新チャット開始時、以下を確認してください:

- [ ] `00_common.md` を読んで全体像を把握
- [ ] `pytest tests/` で全テストパス確認 (現状165件)
- [ ] `data/test_data.csv` の存在確認
- [ ] `config/classification_codes.yaml` のバージョン確認 (v0.2.0)
- [ ] `.env` 設定 (このチャットでは Dify接続不要だが、共通の前提として)

---

## チャット2への引き継ぎ完了条件

このチャットの作業が完了したと言えるのは:

- [ ] `outputs/prepared_data/{timestamp}_records.json` が生成されている
- [ ] records.json のスキーマが上記契約と一致している
- [ ] split処理に重大な警告 (marker_mismatch多発等) がない
- [ ] split_full.parquet と meta.json も同じタイムスタンプで存在する

これらが揃ったら、その `{timestamp}` をチャット2に伝えて引き継ぎ完了。
