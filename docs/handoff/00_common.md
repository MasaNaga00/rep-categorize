# 共通基盤ドキュメント

> このドキュメントは、3つの実装チャット(データ準備 / Dify実行 / 結果出力)すべての最初に共有される共通基盤です。
> プロジェクト概要、設計判断、ディレクトリ構成、命名規則を定義します。

---

## プロジェクト概要

### 目的

カメラ修理データの故障分類を自動化する。修理データには `①②③` で複数故障現象が区切られており、ユーザコメント・修理者コメントから以下を分類する:

- **故障分類** (ユーザ視点 / 修理者視点の2軸)
- **環境要因** (water, sand_dust, impact, heat, cold, humidity, none, unknown の多値)
- **再現状況** (reproduced, not_reproduced, partial, not_attempted)

最終的にメーカー責任 (manufacturer / user_or_unknown) を判定し、Tableau で可視化する。

### 技術スタック

- Python 3.11+, pydantic v2, Jinja2, pandas
- LLM: OpenAI GPT-4o (Dify ワークフロー経由)
- Dify: セルフホスティング版
- データ可視化: Tableau Desktop

### 全体アーキテクチャ

```
[CSV] 
   ↓ ColumnMapping
[split.py: ①②③で分割]
   ↓ (repair_id, sub_id) 主キー
[zone_extractor.py: preamble/marker_zone/postamble分解]
   ↓ list[dict]
[prompt_builder.py: Dify用プロンプト生成 (※プロンプトはDifyに固定済み)]
   ↓ records.json
[dify_client.py: Dify Workflow API呼び出し]
   ↓ classifications.json
[derive_metrics.py: 派生指標計算 (responsibility, perspective_match等)]
   ↓ 
[output_formatter.py: 3形式CSV出力 (ワイド/ロング/集約)]
   ↓
[Tableau Desktop / DB]
```

---

## チャット分割と境界

| # | チャット名 | 役割 |
|---|---|---|
| 1 | データ準備 | CSV読み込み → records.json 生成 |
| 2 | Dify実行 | records.json → classifications.json |
| 3 | 結果出力 | records.json + classifications.json → 最終CSV |

### 境界A: チャット1 → チャット2

```
outputs/prepared_data/{timestamp}_records.json    # チャット2の入力
outputs/prepared_data/{timestamp}_split_full.parquet  # 参考用全カラム
outputs/prepared_data/{timestamp}_meta.json       # split統計、警告
```

### 境界B: チャット2 → チャット3

```
outputs/dify_results/{timestamp}_classifications.json  # チャット3の入力
outputs/dify_results/{timestamp}_classifications.parquet
outputs/dify_results/{timestamp}_failed_records.json   # 失敗バッチの再実行用
outputs/dify_results/{timestamp}_run_meta.json
```

### {timestamp} 命名規則

- 形式: `YYYYMMDD_HHMMSS` (例: `20260506_143012`)
- 1回のパイプライン実行で同じタイムスタンプを使う(チャット1とチャット2の対応が分かりやすい)
- チャット3は両方のファイルを (repair_id, sub_id) で join

---

## 確定した重要設計判断

過去の議論で確定済み。**新しいチャットでこれらを再議論しない**こと。

### データ加工

| 判断 | 内容 |
|---|---|
| 分割マーカー | 全角丸数字 ①②③④⑤⑥⑦⑧⑨⑩ をプライマリ、半角番号 (1)等は警告のみで分割しない |
| 主キー | `(repair_id, sub_id)` の複合キー、sub_id は1始まり |
| 4ゾーン設計 | テキストを `preamble / bracket_prefix / marker_zone / postamble` に分解 |
| 連続マーカー | `①②` のグループ化、同チャンクを複数sub_idに複製 |
| 重複マーカー | 同じ番号が複数回出現する場合は警告して不分割 |
| ユーザの【】 | マーカー前: 除去 (ノイズ扱い) / マーカーゾーン内: 除去 |
| 修理者の【】 | マーカー前: bracket_prefix に保持 / マーカーゾーン内: 保持 (部品情報) |
| postamble | ※/▪/🔳等で始まる後置きを `_postamble` カラムに分離 |
| ML/LENS判定 | SQL段階で完了済み。本パイプラインでは判定しない (`product_type` カラムを信用) |

### LLM・プロンプト

| 判断 | 内容 |
|---|---|
| LLMモデル | OpenAI GPT-4o (Dify経由) |
| プロンプト管理 | Difyワークフロー内に固定。`prompt_builder.py` は貼り付け用テキスト生成ツール |
| 入力データ形式 | JSON (取り違え防止) |
| 製品種別分岐 | Difyワークフローの if/else で実装 (Pythonからは単一エンドポイント呼出) |
| バッチサイズ | 10件単位を推奨 |
| responsibility | プロンプトには載せない (コードから決定論的に派生) |

### コード設計

| 判断 | 内容 |
|---|---|
| 環境変数 | `.env` で管理。`DIFY_BASE_URL`, `DIFY_API_KEY` |
| 認証 | 引数 + 環境変数の両対応 |
| 同期/非同期 | dify_client は両APIを提供 |
| エラー時 | リトライ → 失敗マークしてスキップ、処理継続 |
| 中間結果 | Parquet と JSON 両方で保存 |

---

## ディレクトリ構成

```
repair_failure_classifier/
├── config/
│   ├── classification_codes.yaml    # 分類コード体系 (全チャットで参照)
│   └── prompts/                     # Jinja2テンプレート (チャット1で生成、チャット2のDifyに貼り付け済み)
│       ├── system_prompt.j2
│       └── user_message.j2
├── src/
│   ├── codes_loader.py              # YAML読込・pydanticバリデーション
│   ├── zone_extractor.py            # 4ゾーン分解 (チャット1)
│   ├── split.py                     # ①②③分割 (チャット1)
│   ├── prompt_builder.py            # Dify貼り付け用テキスト生成 (チャット1のみ。チャット2/3は触らない)
│   ├── dify_client.py               # Dify API クライアント (チャット2)
│   ├── derive_metrics.py            # 派生指標計算 (チャット3、未実装)
│   └── output_formatter.py          # 3形式CSV出力 (チャット3、未実装)
├── tests/
│   ├── conftest.py
│   ├── test_codes_loader.py         # 28件パス
│   ├── test_zone_extractor.py       # 30件パス
│   ├── test_split.py                # 38件パス
│   ├── test_prompt_builder.py       # 35件パス
│   ├── test_dify_client.py          # 34件パス
│   ├── test_derive_metrics.py       # チャット3で追加
│   └── test_output_formatter.py     # チャット3で追加
├── notebooks/
│   ├── 01_split_validation.ipynb    # 分割パターン検証
│   ├── 02_pilot_run.ipynb           # 実データ疎通確認 (現状ここまで完了)
│   ├── 03_main_run.ipynb            # 本処理 (チャット3で作成)
│   └── 04_quality_review.ipynb      # 品質レビュー (チャット3で作成)
├── examples/
│   ├── codes_loader_demo.py
│   ├── prompt_builder_demo.py
│   ├── dify_client_demo.py
│   └── dify_curl_test.sh
├── docs/
│   ├── architecture_design.md       # 全体設計書
│   ├── dify/                        # Dify構築手順書
│   │   ├── dify_workflow_setup.md
│   │   └── test_payloads/
│   └── handoff/                     # チャット引き継ぎドキュメント (本ファイル群)
│       ├── 00_common.md             # ← これ
│       ├── 01_data_prep.md
│       ├── 02_dify_execution.md
│       └── 03_result_output.md
├── outputs/
│   ├── dify_prompts/                # prompt_builder の出力 (Dify貼り付け済み)
│   ├── prepared_data/               # チャット1の出力
│   ├── dify_results/                # チャット2の出力
│   └── final/                       # チャット3の出力
├── data/
│   └── test_data.csv                # 検証用CSV
├── .env.example
├── .env                             # 機密情報、gitignore
└── .gitignore
```

---

## classification_codes.yaml の概要

| 項目 | 内容 |
|---|---|
| バージョン | 0.2.0 |
| ML 故障コード | 47項目 (M001-M046, M_UNK) |
| LENS 故障コード | 37項目 (L001-L036, L_UNK) |
| 環境要因 | 8種 (water, sand_dust, impact, heat, cold, humidity, none, unknown) |
| 再現状況 | 4種 (reproduced, not_reproduced, partial, not_attempted) |
| 全コードに `responsibility` 属性付与 | manufacturer or user_or_unknown |

特殊コード:
- `M046` / `L036` : OTHER (該当なし)
- `M_UNK` / `L_UNK` : UNKNOWN (判定不能)
- `M042-M045` / `L032-L035` : サービスレコード (検査・クリーニング・OH・FU)

判定ルール付きコード:
- `M012` (センサー内ゴミ) / `M013` (センサーゴミ): 修理者が分解確認したか否かで区別、迷ったら M013
- `M014` (ファインダー内) / `M015` (ファインダー外): 同様

---

## テスト・実行環境

### 必要な依存

```bash
pip install pandas pydantic jinja2 pyyaml requests aiohttp tenacity python-dotenv
# テスト用
pip install pytest pytest-asyncio requests-mock aioresponses
# Notebook用
pip install jupyter
```

### .env (機密情報、新チャットでも必要)

```
DIFY_BASE_URL=https://your-dify-host
DIFY_API_KEY=app-xxxxx
```

### テスト実行

```bash
cd repair_failure_classifier
python3 -m pytest tests/
# 現状: 165件パス (チャット3完成後に増える想定)
```

### CLI ツール

```bash
# プロンプト生成 (Dify貼り付け用)
PYTHONPATH=src python3 -m prompt_builder

# Dify疎通確認 (curl)
./examples/dify_curl_test.sh 01_ml_simple
```

---

## チャット間で守るべきルール

1. **既存テストを壊さない**: 各チャットでコード変更時、必ず `pytest tests/` を実行して全件パスを確認
2. **YAML スキーマを変更しない**: classification_codes.yaml の構造変更は影響範囲が大きい (codes_loader.py のpydantic定義から見直し必要)。必要なら全チャット間で合意すること
3. **境界の入出力契約を守る**: チャット2の出力スキーマを変更したい場合、チャット3の引き継ぎ資料も同期更新する
4. **{timestamp} を共有**: 1パイプライン実行で同じタイムスタンプを使う
5. **新しい設計判断は記録**: チャット内で重要判断をしたら、引き継ぎ資料を更新する

---

## 過去の重要なやり取り抜粋

新チャットで議論を蒸し返さないため、特に重要な議論結果を残しておきます:

### Q: メーカー責任判定をLLMにやらせるか?
A: **NO**。コードから決定論的に派生する。`derive_metrics.py` で `is_manufacturer_responsibility_user/repair` を計算する。

### Q: プロンプトを毎回API経由で送るか、Difyに固定するか?
A: **Dify固定**。更新頻度が低いので運用負荷より一貫性を優先。

### Q: ML/LENSの分岐はPython側かDify側か?
A: **Dify側のif/else**。1ワークフロー内で完結。

### Q: 修理データのDify送信形式は自然文かJSONか?
A: **JSON**。①等のマーカー文字との取り違え防止のため。

### Q: 連続マーカー (`①②`) のまとめ回答の処理は?
A: **同じチャンクを両sub_idに複製**。MarkerGroupでグループ化して展開。

### Q: 重複マーカー (`③` が複数回出現) の処理は?
A: **警告して分割せず1レコード扱い**。安全側に倒す。

---

## 現在の進捗状況 (2026年5月時点)

| モジュール | 状態 | 備考 |
|---|---|---|
| classification_codes.yaml | ✅ v0.2.0完成 | 全コードに responsibility 付与済み |
| codes_loader.py | ✅ 完成 | 28件テストパス |
| zone_extractor.py | ✅ 完成 | 30件テストパス |
| split.py | ✅ 完成 | 38件テストパス、4ゾーン対応 |
| prompt_builder.py | ✅ 完成 | 35件テストパス、CLI対応 |
| dify_client.py | ✅ 完成 | 34件テストパス、同期/非同期両対応 |
| Difyワークフロー | ⏸ 構築待ち | 手順書 (`docs/dify/dify_workflow_setup.md`) は完成 |
| 02_pilot_run.ipynb | ✅ 完成 | 実機接続待ち |
| derive_metrics.py | ❌ 未実装 | チャット3で実装 |
| output_formatter.py | ❌ 未実装 | チャット3で実装 |
| 03_main_run.ipynb | ❌ 未実装 | チャット3で実装 |
| 04_quality_review.ipynb | ❌ 未実装 | チャット3で実装 |

---

## 用語集

| 用語 | 意味 |
|---|---|
| repair_id | 修理ID。実データのCSVでは `ID` カラム |
| sub_id | 分割番号。1始まり |
| ML | ミラーレスカメラ |
| LENS | 交換レンズ |
| マーカー | ①②③等の全角丸数字 |
| 連続マーカー | `①②` のように間に文字を挟まず連続するマーカー |
| 4ゾーン | preamble / bracket_prefix / marker_zone / postamble |
| サービスレコード | 検査・クリーニング・OH・ファームアップ等の故障以外のレコード |
| OH | オーバーホール |
| FU | ファームアップ |
| responsibility | manufacturer (メーカー責任) / user_or_unknown (ユーザ起因または不明) |
