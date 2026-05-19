# チャット3: 結果出力チャット引き継ぎ資料

> このチャットの役割: チャット1の records.json とチャット2の classifications.json を結合し、派生指標を計算して、Tableau 用 CSV や DB 追記用 CSV を出力します。
> **先に `00_common.md` を確認してください**。共通基盤情報はそちらに記載されています。

---

## このチャットの役割

```
チャット1から受領: records.json
チャット2から受領: classifications.json
                   ↓
[結合 (repair_id, sub_id)] → [派生指標計算] → [CSV出力 (3形式)]
                   ↓
              Tableau / DB
```

### このチャットでやること

- `records.json` と `classifications.json` を読み込んで結合
- 派生指標を計算 (perspective_match、is_manufacturer_responsibility 等)
- 3形式の CSV を出力 (ワイド/ロング/集約)
- `derive_metrics.py` の実装 (新規)
- `output_formatter.py` の実装 (新規)
- 03_main_run.ipynb / 04_quality_review.ipynb の作成 (新規)

### このチャットでやらないこと

- ❌ 分割処理の変更 (チャット1の責務、固定済み)
- ❌ Dify 呼び出し (チャット2の責務、固定済み)
- ❌ プロンプト・分類体系の変更
- ❌ Tableau ダッシュボードの作成 (Tableau 側の作業、チャットの守備範囲外)

---

## 入力契約 (チャット1とチャット2からの受領)

### 入力ファイル

```
# チャット1からの入力
outputs/prepared_data/{timestamp}_records.json
outputs/prepared_data/{timestamp}_split_full.parquet  # 参考、postamble確認用

# チャット2からの入力
outputs/dify_results/{timestamp}_classifications.json

# 両者は同じ {timestamp} で対応する
```

### records.json のスキーマ (チャット1の出力)

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

### classifications.json のスキーマ (チャット2の出力)

```json
[
  {
    "repair_id": "R016",
    "sub_id": 1,
    "user_perspective": {
      "failure_category_code": "M020",
      "confidence": 0.85,
      "evidence": "...",
      "insufficient_info": false
    },
    "repair_perspective": {
      "failure_category_code": "M020",
      "confidence": 0.95,
      "evidence": "...",
      "insufficient_info": false
    },
    "reproduction_status": "reproduced",
    "reproduction_evidence": "...",
    "reproduction_confidence": 0.7,
    "environment_factors": ["unknown"],
    "environment_evidence_source": {"unknown": "user"},
    "environment_evidence": {"unknown": "..."},
    "environment_confidence": {"unknown": 1.0}
  }
]
```

### 結合方法

```python
import json
import pandas as pd

records = json.loads(records_path.read_text(encoding="utf-8"))
classifications = json.loads(classifications_path.read_text(encoding="utf-8"))

records_df = pd.DataFrame(records)
class_df = pd.json_normalize(classifications, sep="_")

# (repair_id, sub_id) で内部結合
merged = records_df.merge(class_df, on=["repair_id", "sub_id"], how="inner")
```

`pd.json_normalize` でネスト構造をフラット化すると、以下のようなカラムに展開される:

- `user_perspective_failure_category_code`
- `user_perspective_confidence`
- `repair_perspective_failure_category_code`
- ... 等

---

## 出力契約

### 出力ファイル

```
outputs/final/
├── {timestamp}_wide.csv        # ワイド形式 (Tableauダッシュボード用、サマリ)
├── {timestamp}_long.csv        # ロング形式 (環境要因縦展開、詳細ドリルダウン)
├── {timestamp}_aggregated.csv  # 集約形式 (repair_id単位、DB追記用)
└── {timestamp}_output_meta.json  # 出力メタ情報
```

### ワイド形式 (Tableauダッシュボード用)

(repair_id, sub_id) 単位の1行。1行ですべての指標が見える。

| カラム | 型 | 説明 |
|---|---|---|
| repair_id | string | 修理ID |
| sub_id | integer | 分割番号 |
| product_type | string | ML or LENS |
| user_text | string | ユーザコメント本文 |
| user_context | string | ユーザコメント前置き |
| repair_text | string | 修理者コメント本文 |
| repair_context | string | 修理者コメント前置き |
| internal_1, internal_2 | string | 内部コメント |
| user_failure_code | string | ユーザ視点コード |
| user_failure_name | string | ユーザ視点コード名 (codes_loaderで取得) |
| user_confidence | float | confidence |
| user_evidence | string | 判定根拠 |
| repair_failure_code | string | 修理者視点コード |
| repair_failure_name | string | 修理者視点コード名 |
| repair_confidence | float | confidence |
| repair_evidence | string | 判定根拠 |
| reproduction_status | string | 再現状況 |
| reproduction_evidence | string | 判定根拠 |
| reproduction_confidence | float | |
| environment_factors | string | カンマ区切り (例: "water,impact") |
| has_water | bool | 環境要因フラグ展開 |
| has_sand_dust | bool | |
| has_impact | bool | |
| has_heat | bool | |
| has_cold | bool | |
| has_humidity | bool | |
| **以下、派生指標** | | |
| perspective_match | bool | user_failure_code == repair_failure_code |
| is_misjudged | bool | ユーザ視点と修理者視点で responsibility が異なる |
| has_harsh_env | bool | 環境要因が none/unknown 以外 |
| has_repair_confirmed_env | bool | repair_evidence_source に "repair" or "both" が含まれる |
| is_manufacturer_responsibility_user | bool | ユーザ視点コードが manufacturer 責任 |
| is_manufacturer_responsibility_repair | bool | 修理者視点コードが manufacturer 責任 |
| is_service_record | bool | 修理者視点が service カテゴリ (検査・OH等) |
| min_confidence | float | 4タスクのconfidence最小値 |

### ロング形式 (環境要因の縦展開)

環境要因ごとに行を分ける形式。Tableauで「環境要因別の故障傾向」のような可視化に便利。

| カラム | 型 | 説明 |
|---|---|---|
| repair_id | string | |
| sub_id | integer | |
| product_type | string | |
| environment_factor | string | 1つの環境要因 |
| environment_evidence_source | string | "user" / "repair" / "both" |
| environment_confidence | float | |
| user_failure_code | string | (短縮版) |
| repair_failure_code | string | |
| reproduction_status | string | |

### 集約形式 (repair_id 単位、DB追記用)

repair_id 単位で集約。修理1件あたりの全体像。

| カラム | 型 | 説明 |
|---|---|---|
| repair_id | string | 主キー |
| product_type | string | |
| total_sub_records | integer | sub_id の数 (=故障現象の数) |
| user_failure_codes | string | カンマ区切り (例: "M005,M007") |
| repair_failure_codes | string | カンマ区切り |
| any_perspective_mismatch | bool | 1件でも視点不一致があるか |
| any_misjudged | bool | 1件でも responsibility 食い違いがあるか |
| any_harsh_env | bool | 1件でも環境要因あり |
| any_manufacturer_responsibility_repair | bool | 1件でもメーカー責任 (修理者視点) |
| all_service_records | bool | 全 sub_id がサービスレコード |
| min_confidence_overall | float | 全 sub_id 中の最小 confidence |

---

## 派生指標の計算ロジック (詳細)

### perspective_match

```python
df["perspective_match"] = (
    df["user_failure_code"] == df["repair_failure_code"]
)
```

### is_misjudged (ユーザと修理者で responsibility が異なる)

```python
df["is_manufacturer_user"] = df["user_failure_code"].apply(
    lambda c: codebook.is_manufacturer_responsibility(c, "ML")  # 製品種別ごとに
)
df["is_manufacturer_repair"] = df["repair_failure_code"].apply(
    lambda c: codebook.is_manufacturer_responsibility(c, "ML")
)
df["is_misjudged"] = df["is_manufacturer_user"] != df["is_manufacturer_repair"]
```

### has_harsh_env

```python
df["has_harsh_env"] = df["environment_factors"].apply(
    lambda factors: any(f not in ("none", "unknown") for f in factors)
)
```

### has_repair_confirmed_env

```python
def _has_repair_confirmed(env_evidence_source: dict) -> bool:
    return any(
        src in ("repair", "both")
        for src in env_evidence_source.values()
    )

df["has_repair_confirmed_env"] = df["environment_evidence_source"].apply(_has_repair_confirmed)
```

### is_service_record

```python
df["is_service_record"] = df["repair_failure_code"].apply(
    lambda c: codebook.get_record_type(c, product_type) == "service"
)
```

サービスレコード判定:
- ML: M042 (検査), M043 (クリーニング), M044 (オーバーホール), M045 (ファームアップ)
- LENS: L032〜L035 (同様)

### min_confidence

```python
df["min_confidence"] = df[[
    "user_confidence",
    "repair_confidence",
    "reproduction_confidence",
    # environment_confidence は dict なので別途処理
]].min(axis=1)

# environment_confidence の最小値も加味
df["env_min_confidence"] = df["environment_confidence"].apply(
    lambda d: min(d.values()) if d else 1.0
)
df["min_confidence"] = df[["min_confidence", "env_min_confidence"]].min(axis=1)
```

### codes_loader のヘルパー関数

`src/codes_loader.py` には以下のヘルパーが既に実装済み:

```python
codebook.is_manufacturer_responsibility(code, product_type) -> bool
codebook.is_valid_failure_code(code, product_type) -> bool
codebook.get_failure_category(code, product_type) -> FailureCategory  # name や record_type を取得
codebook.get_failure_codes_for_product(product_type) -> dict[str, FailureCategory]
```

---

## 完成済みコード (このチャットでは触らない)

| ファイル | 状態 | 役割 |
|---|---|---|
| `src/codes_loader.py` | ✅ 完成 | コード体系読み込み、ヘルパー関数 |
| `src/split.py` | ✅ 完成 | 分割処理、固定済み |
| `src/dify_client.py` | ✅ 完成 | Dify呼び出し、固定済み |

### codes_loader.py の使い方 (再確認)

```python
from codes_loader import load_codes

codebook = load_codes("config/classification_codes.yaml")

# メーカー責任判定
is_mfr = codebook.is_manufacturer_responsibility("M020", "ML")  # bool

# コード妥当性
valid = codebook.is_valid_failure_code("M020", "ML")  # bool

# コード詳細取得
fc = codebook.get_failure_category("M020", "ML")
# fc.name, fc.description, fc.record_type, fc.responsibility, fc.decision_rule
```

---

## このチャットの残タスク

### 優先度高

- [ ] **`src/derive_metrics.py` の実装**

  最低限の関数:
  ```python
  from codes_loader import CodeBook
  import pandas as pd

  def merge_records_and_classifications(
      records: list[dict],
      classifications: list[dict],
  ) -> pd.DataFrame:
      """records と classifications を (repair_id, sub_id) で結合"""
      ...

  def calculate_derived_metrics(
      df: pd.DataFrame,
      codebook: CodeBook,
  ) -> pd.DataFrame:
      """派生指標を追加した DataFrame を返す"""
      df = df.copy()
      df["perspective_match"] = ...
      df["is_manufacturer_responsibility_user"] = ...
      df["is_manufacturer_responsibility_repair"] = ...
      df["is_misjudged"] = ...
      df["has_harsh_env"] = ...
      df["has_repair_confirmed_env"] = ...
      df["is_service_record"] = ...
      df["min_confidence"] = ...
      return df

  def expand_environment_flags(df: pd.DataFrame) -> pd.DataFrame:
      """環境要因を has_water, has_impact 等のフラグに展開"""
      ...

  def add_failure_names(df: pd.DataFrame, codebook: CodeBook) -> pd.DataFrame:
      """user_failure_code → user_failure_name 等を追加"""
      ...
  ```

- [ ] **`src/output_formatter.py` の実装**

  ```python
  from pathlib import Path
  import pandas as pd

  def to_wide_format(df: pd.DataFrame) -> pd.DataFrame:
      """ワイド形式 (Tableau用) に変換"""
      ...

  def to_long_format(df: pd.DataFrame) -> pd.DataFrame:
      """ロング形式 (環境要因縦展開) に変換"""
      ...

  def to_aggregated_format(df: pd.DataFrame, codebook) -> pd.DataFrame:
      """repair_id 単位の集約形式 (DB追記用)"""
      ...

  def write_all_formats(
      df: pd.DataFrame,
      output_dir: Path,
      timestamp: str,
      codebook: CodeBook,
  ) -> dict[str, Path]:
      """3形式すべてをCSV出力"""
      ...
  ```

- [ ] **テスト実装**
  - `tests/test_derive_metrics.py`
  - `tests/test_output_formatter.py`
  - 既存テストとの整合性 (165件全てパス維持)

- [ ] **`notebooks/03_main_run.ipynb` の作成**
  - records.json + classifications.json 読み込み
  - merge → derive_metrics → output_formatter
  - 3形式CSV保存
  - 実行サマリ表示

### 優先度中

- [ ] **`notebooks/04_quality_review.ipynb` の作成**
  - 分類精度のサンプルレビュー
  - confidence 低い件の抽出
  - perspective_match=False のサンプル確認
  - LLMハルシネーション検出 (体系外コード等)

- [ ] **集計サマリ機能**
  - 全体トレンド: 何故メーカー責任率が何%、再現率が何%等
  - 製品種別ごとの故障コード上位10
  - 環境要因の出現頻度

### 優先度低

- [ ] Tableau用のテンプレート提供 (.twb ファイル等は守備範囲外だが、推奨ダッシュボード構成のドキュメント化は可)
- [ ] 全期間累積データのインクリメンタル更新ロジック

---

## 関連ファイル一覧

### このチャットで参照・編集するファイル

```
# 参照
config/classification_codes.yaml
src/codes_loader.py

# 新規実装
src/derive_metrics.py     # ★新規
src/output_formatter.py   # ★新規
tests/test_derive_metrics.py     # ★新規
tests/test_output_formatter.py   # ★新規
notebooks/03_main_run.ipynb       # ★新規
notebooks/04_quality_review.ipynb # ★新規

# 入力 (両チャットから受領)
outputs/prepared_data/{timestamp}_records.json
outputs/dify_results/{timestamp}_classifications.json

# 出力
outputs/final/{timestamp}_wide.csv
outputs/final/{timestamp}_long.csv
outputs/final/{timestamp}_aggregated.csv
outputs/final/{timestamp}_output_meta.json
```

### このチャットでは触らないファイル

```
src/split.py              # チャット1の領域 (固定済み)
src/zone_extractor.py     # チャット1の領域 (固定済み)
src/dify_client.py        # チャット2の領域 (固定済み)
src/prompt_builder.py     # チャット1の領域 (固定済み)
docs/dify/                # チャット2の領域
config/prompts/           # 固定済み
```

---

## 設計のコツと注意点

### 1. environment_factors の扱い

LLMの出力では `environment_factors` がリスト、`environment_evidence_source` 等が dict。これを処理する際:

- pandas に取り込むと object 型になる (dict のまま)
- フラグ展開 (`has_water` 等) するときは `apply()` で処理
- ロング形式に変換するときは `explode()` で行に展開

```python
# 例: ロング形式変換
long_df = df.explode("environment_factors").rename(
    columns={"environment_factors": "environment_factor"}
)
# 各行で environment_evidence_source[environment_factor] を取り出す
```

### 2. 製品種別別の処理

`is_manufacturer_responsibility` 等は製品種別 (ML/LENS) でコード体系が違う。各レコードの `product_type` を見て分岐させる:

```python
df["is_manufacturer_user"] = df.apply(
    lambda row: codebook.is_manufacturer_responsibility(
        row["user_failure_code"], row["product_type"],
    ),
    axis=1,
)
```

### 3. NaN・欠損の扱い

LLMが余計な前置きを返した結果、一部フィールドが欠落している可能性がある。

- 欠損があれば `validation_issues` に記録
- フラグ系は欠損時 False (or None) で埋める
- min_confidence は欠損時 NaN にする

### 4. Tableau との連携

ワイド形式CSVを Tableau Desktop で読み込み、以下のフィールドを使う想定:

- カテゴリ軸: product_type, user_failure_name, repair_failure_name
- メジャー軸: count, avg(min_confidence), 各種フラグの平均 (=率)
- フィルタ: has_harsh_env, is_misjudged 等のフラグ

UTF-8 で出力すること (Tableauが日本語を正しく読むため)。

### 5. 失敗データの扱い

チャット2で失敗したバッチのレコードは classifications.json に含まれない。
チャット3で merge する際:
- `how="inner"` だと失敗レコードは脱落 (default、推奨)
- `how="left"` だと records 側を残し、分類カラムが NaN になる

一旦 inner でよいが、件数差が大きい場合は warning 出力する。

---

## 引き継ぎ時の確認事項

新チャット開始時、以下を確認してください:

- [ ] `00_common.md` を読んで全体像を把握
- [ ] チャット1から `{timestamp}_records.json` の場所を共有してもらう
- [ ] チャット2から `{timestamp}_classifications.json` の場所を共有してもらう (同じtimestamp)
- [ ] `pytest tests/` で 165件パス確認
- [ ] `config/classification_codes.yaml` のバージョン確認 (v0.2.0)
- [ ] `codes_loader` の `is_manufacturer_responsibility` 等のヘルパーが期待通り動作するか単体確認

---

## 完了条件

このチャットの作業が完了したと言えるのは:

- [ ] `src/derive_metrics.py` 実装完了、テストパス
- [ ] `src/output_formatter.py` 実装完了、テストパス
- [ ] `outputs/final/{timestamp}_wide.csv` が生成され、Tableau で読み込める
- [ ] `outputs/final/{timestamp}_long.csv`, `{timestamp}_aggregated.csv` も同様
- [ ] `notebooks/03_main_run.ipynb` でエンドツーエンド実行できる
- [ ] 全テストパス維持 (チャット1, 2 のテストも壊さない)

これらが揃ったら、Tableau での可視化作業に進める状態。
