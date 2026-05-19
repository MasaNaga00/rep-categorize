# Dify ワークフロー設計手順

このディレクトリには、Dify の修理データ分類ワークフローに貼り付けるための
プロンプトテンプレートが含まれます。

## ファイル一覧

| ファイル | 用途 |
|---|---|
| `system_prompt_ML.txt` | ML分岐の LLM ノードの「System」に貼り付け |
| `system_prompt_LENS.txt` | LENS分岐の LLM ノードの「System」に貼り付け |
| `user_prompt_template.txt` | 両 LLM ノードの「User」に貼り付け（共通） |

## Dify ワークフロー構成（推奨）

```
[開始]
  入力変数:
    - records_json (string, 必須): 分類対象レコードのJSON配列
    - n_records (number, 必須): レコード件数
    - product_type (string, 必須): "ML" または "LENS"

[If/Else 分岐]
  条件: product_type == "ML"

  ↓ true ブランチ                    ↓ false ブランチ
  [LLM ノード: ML]                   [LLM ノード: LENS]
    System: system_prompt_ML.txt    System: system_prompt_LENS.txt
    User:   user_prompt_template.txt User:   user_prompt_template.txt
  ↓                                  ↓
  [変数を集約]
    出力: result (string, LLMの応答JSON)

[終了]
  出力: result
```

## 入力変数の準備（Python 側）

```python
import json
records = split_df.to_dict("records")  # split.py の出力
payload = {
    "records_json": json.dumps(records, ensure_ascii=False, indent=2),
    "n_records": len(records),
    "product_type": records[0]["product_type"],  # バッチ内で同一を保証する前提
}
# Dify ワークフロー実行 API にこの payload を渡す
```

## プロンプト更新時の運用

1. `config/classification_codes.yaml` を編集
2. プロジェクトルートで `python -m src.prompt_builder` を実行
3. このディレクトリの .txt ファイルが更新される
4. Dify 管理画面の各 LLM ノードのプロンプトを上書き
5. Dify 上で動作確認

## 注意事項

- Dify の変数表記は `{{records_json}}` のように二重中括弧。
  user_prompt_template.txt 内のプレースホルダがそのまま使われる前提。
- 環境変数で別の表記が必要な場合は user_message.j2 を調整して再生成。
- LLM のモデル設定（temperature 等）は Dify 側のノード設定で行う。推奨:
  - temperature: 0.1〜0.2（一貫性重視）
  - response_format: JSON object（対応モデルなら有効化）
  - max_tokens: 8000（10件バッチ想定）
