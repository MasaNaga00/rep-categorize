# Dify ワークフロー構築手順書

カメラ修理データの故障分類ワークフローを Dify 上に構築する手順。

**前提環境**:
- Dify: セルフホスティング版
- LLM: OpenAI GPT-4 / GPT-4o
- 分岐方式: 1つのワークフローで if/else（ML/LENS）

---

## 1. 全体構成

### 1.1 ワークフロー図

```
┌─────────────────┐
│ [開始ノード]    │
│                 │
│ 入力変数:       │
│ - records_json  │
│ - n_records     │
│ - product_type  │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────┐
│ [If/Else ノード]            │
│ 条件: product_type == "ML"  │
└──────┬─────────────────┬────┘
       │                 │
       │ true            │ false
       ▼                 ▼
┌───────────────┐ ┌────────────────┐
│ [LLM ノード:  │ │ [LLM ノード:    │
│  ML分類]      │ │  LENS分類]      │
│               │ │                 │
│ system: ML用  │ │ system: LENS用  │
│ user: 共通    │ │ user: 共通      │
└──────┬────────┘ └────────┬────────┘
       │                   │
       └─────────┬─────────┘
                 │
                 ▼
┌─────────────────────────────┐
│ [変数アサイナーノード]      │
│ 両分岐の出力を統合          │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ [終了ノード]                │
│ 出力: result (string)       │
└─────────────────────────────┘
```

### 1.2 入出力仕様

**入力（Pythonクライアントから渡す）**:

| 変数名 | 型 | 必須 | 説明 |
|---|---|---|---|
| `records_json` | string | ✓ | 分類対象レコードのJSON配列文字列 |
| `n_records` | number | ✓ | レコード件数 |
| `product_type` | string | ✓ | "ML" または "LENS" |

**出力**:

| 変数名 | 型 | 説明 |
|---|---|---|
| `result` | string | LLMの応答（JSON配列文字列） |

---

## 2. 事前準備

### 2.1 必要ファイルの生成

プロジェクトルートで以下を実行:

```bash
cd repair_failure_classifier
PYTHONPATH=src python3 -m prompt_builder
```

`outputs/dify_prompts/` に以下が生成される:

- `system_prompt_ML.txt`     - ML分類用のシステムプロンプト
- `system_prompt_LENS.txt`   - LENS分類用のシステムプロンプト
- `user_prompt_template.txt` - ユーザプロンプトテンプレート（共通）
- `README.md`                - 簡易リファレンス

### 2.2 OpenAI モデルプロバイダーの登録

Dify管理画面 → 右上のユーザーアイコン → 「設定」 → 「モデルプロバイダー」 → 「OpenAI」 を選択

API キーを設定:

```
API Key: sk-xxx...
Organization ID: （任意）
Custom API Endpoint: （標準のOpenAI APIなら空欄）
```

「保存」して動作確認。

### 2.3 推奨モデル設定

| モデル | 用途 | 備考 |
|---|---|---|
| `gpt-4o` | 本番用（推奨） | バランス良、JSON Mode対応、価格妥当 |
| `gpt-4o-mini` | 検証・小規模テスト用 | 安価、検証段階で活用 |
| `gpt-4-turbo` | 高精度が必要な場合 | コスト高 |

---

## 3. ワークフロー作成手順

### 3.1 アプリケーション作成

1. Dify管理画面のトップ → 「アプリを作成」
2. 「ワークフロー」を選択（「チャットフロー」ではない）
3. アプリ名: `repair_failure_classifier`
4. 説明: `カメラ修理データの故障分類`
5. アイコン: 任意

### 3.2 開始ノードの設定

開始ノードをクリックして「入力フィールド」を追加:

#### records_json
- フィールドタイプ: **段落（Paragraph）**
- 変数名: `records_json`
- ラベル: `レコードJSON`
- 必須: ✓
- 最大長: 50000（10件バッチ想定で十分な余裕）

#### n_records
- フィールドタイプ: **数値（Number）**
- 変数名: `n_records`
- ラベル: `レコード件数`
- 必須: ✓
- 最小: 1
- 最大: 50

#### product_type
- フィールドタイプ: **選択（Select）**
- 変数名: `product_type`
- ラベル: `製品種別`
- 必須: ✓
- オプション:
  - `ML`
  - `LENS`

> **注意**: フィールド名は厳密に上記と一致させること（Pythonクライアントが参照する）。

### 3.3 If/Else ノードの追加

開始ノード後にIf/Elseノードを追加。

**条件設定**:
- 変数: `{{#start.product_type#}}`
- 演算子: `is`
- 値: `ML`

→ true ブランチ: ML分類用LLMノードへ
→ false ブランチ: LENS分類用LLMノードへ

### 3.4 LLMノード設定（ML側）

If/Elseのtrueブランチ後にLLMノードを追加。

#### モデル設定
- モデルプロバイダー: OpenAI
- モデル: `gpt-4o` （推奨）
- **モデルパラメータ**:
  - Temperature: `0.1`
  - Top P: `1.0`
  - Max Tokens: `8000`
  - Frequency Penalty: `0`
  - Presence Penalty: `0`
  - **Response Format: `JSON Object`** ← 重要

#### プロンプト設定

**System プロンプト**:
- `outputs/dify_prompts/system_prompt_ML.txt` の中身を全コピーして貼り付け

**User プロンプト**:
- `outputs/dify_prompts/user_prompt_template.txt` の中身を全コピーして貼り付け
- 貼り付け後、Dify上で `{{records_json}}` と `{{n_records}}` がDifyの変数として認識されているか確認
- 認識されていない場合は、変数挿入ボタンから明示的に指定:
  - `{{#start.records_json#}}` → records_json
  - `{{#start.n_records#}}` → n_records

> **JSON Mode 利用時の注意**: OpenAI のJSON Modeを有効にする場合、システムプロンプトに `JSON` の単語を含める必要があります（OpenAI API仕様）。生成済み system_prompt_ML.txt には複数箇所で「JSON」が出てくるので問題なし。

#### 出力変数
- 変数名: `text` （Difyのデフォルト）

### 3.5 LLMノード設定（LENS側）

ML側と同じ手順で、ただし:
- System プロンプト: `system_prompt_LENS.txt` を貼り付け
- 他の設定は ML と同じ

### 3.6 変数アサイナーノードの追加

両LLMノードの出力を統合する。

「変数アサイナー」ノードを追加:
- 出力変数名: `result`
- 型: string

割り当て:
- ML分類LLMノードの `text` または LENS分類LLMノードの `text` のどちらかが入る
- Difyのワークフロー上では、実行されなかった分岐のノード出力はnullになる
- 「最初の非null値」を取る設定にする

### 3.7 終了ノードの設定

- 出力変数: `result` （変数アサイナーから受け取る）
- 型: string

### 3.8 ワークフローを保存

右上の「公開」ボタン → 「公開」

---

## 4. 動作確認（管理画面でテスト実行）

### 4.1 ワークフロー画面でテスト

「実行」ボタン → 「テスト実行」

入力例:

```json
{
  "records_json": "[{\"repair_id\":\"TEST001\",\"sub_id\":1,\"user_text\":\"AFが効きません\",\"user_context\":\"\",\"repair_text\":\"AFユニット交換にて復旧\",\"repair_context\":\"\",\"internal_1\":\"\",\"internal_2\":\"\"}]",
  "n_records": 1,
  "product_type": "ML"
}
```

**期待される出力**:

```json
[
  {
    "repair_id": "TEST001",
    "sub_id": 1,
    "user_perspective": {
      "failure_category_code": "M005",
      "confidence": 0.9,
      "evidence": "ユーザ「AFが効きません」より",
      "insufficient_info": false
    },
    "repair_perspective": {
      "failure_category_code": "M005",
      "confidence": 0.95,
      "evidence": "修理者「AFユニット交換」より",
      "insufficient_info": false
    },
    "reproduction_status": "reproduced",
    "reproduction_evidence": "修理者がAFユニット交換実施",
    "reproduction_confidence": 0.7,
    "environment_factors": ["unknown"],
    "environment_evidence_source": {"unknown": "user"},
    "environment_evidence": {"unknown": "環境記述なし"},
    "environment_confidence": {"unknown": 1.0}
  }
]
```

### 4.2 LENS側のテスト

```json
{
  "records_json": "[{\"repair_id\":\"TEST002\",\"sub_id\":1,\"user_text\":\"ズームリングが固い\",\"user_context\":\"\",\"repair_text\":\"ズームリング洗浄調整\",\"repair_context\":\"\",\"internal_1\":\"\",\"internal_2\":\"\"}]",
  "n_records": 1,
  "product_type": "LENS"
}
```

期待: `failure_category_code` に L008 (ズーム引掛り/作動不具合) のような LENS 用コードが返る。

### 4.3 動作確認チェックリスト

- [ ] ML側で M-prefixed コードが返る（M001 など）
- [ ] LENS側で L-prefixed コードが返る（L001 など）
- [ ] 出力が valid な JSON 配列になっている
- [ ] 入力件数と出力配列の件数が一致
- [ ] `repair_id` / `sub_id` が入力と一致
- [ ] `user_perspective` と `repair_perspective` の両方が含まれる
- [ ] `reproduction_status` が4種類のいずれか
- [ ] `environment_factors` がリスト形式

---

## 5. API公開設定

### 5.1 APIキーの取得

ワークフロー画面左メニュー → 「API アクセス」

「APIキー」セクションで「+ APIキーを作成」

- 名前: `production_key` 等
- 表示されたキー（`app-xxx...`）を保存（**この画面でしか確認できない**）

### 5.2 エンドポイントURL

セルフホスティングの場合、ベースURLは社内ホスト名:

```
https://<your-dify-host>/v1/workflows/run
```

ヘッダ:
```
Authorization: Bearer app-xxx...
Content-Type: application/json
```

ペイロード:
```json
{
  "inputs": {
    "records_json": "[...]",
    "n_records": 1,
    "product_type": "ML"
  },
  "response_mode": "blocking",
  "user": "python_client"
}
```

### 5.3 curl テスト

```bash
curl -X POST 'https://<your-dify-host>/v1/workflows/run' \
  -H 'Authorization: Bearer app-xxx...' \
  -H 'Content-Type: application/json' \
  -d '{
    "inputs": {
      "records_json": "[{\"repair_id\":\"TEST001\",\"sub_id\":1,\"user_text\":\"AFが効きません\",\"user_context\":\"\",\"repair_text\":\"AFユニット交換\",\"repair_context\":\"\",\"internal_1\":\"\",\"internal_2\":\"\"}]",
      "n_records": 1,
      "product_type": "ML"
    },
    "response_mode": "blocking",
    "user": "test_user"
  }'
```

レスポンス例:
```json
{
  "workflow_run_id": "...",
  "task_id": "...",
  "data": {
    "id": "...",
    "workflow_id": "...",
    "status": "succeeded",
    "outputs": {
      "result": "[{...}]"
    },
    "error": null,
    "elapsed_time": 5.234,
    "total_tokens": 8123,
    "total_steps": 5,
    "created_at": ...,
    "finished_at": ...
  }
}
```

`outputs.result` がLLMの応答JSON文字列（パース必要）。

---

## 6. トラブルシューティング

### 6.1 LLMが余計な説明文を返す（JSONパース失敗）

**症状**:
```
"以下に分類結果を示します:\n\n```json\n[...]\n```"
```

**原因**: GPT-4o は親切設計で、明示しないと前置きを付ける。

**対処**:
1. **JSON Mode を有効化**（Response Format: JSON Object）
2. JSON Mode で「JSON配列」が返らない場合は、システムプロンプトでオブジェクトとして包む形に調整
3. システムプロンプトの最後に強い指示を追加: `Return only the JSON array. No prose, no markdown.`

> **GPT-4o JSON Mode の注意**: JSON Mode は **配列ではなくオブジェクト** を返すことを保証する仕様。配列で返してほしい場合、トップレベルを `{"results": [...]}` の形にしてからPython側で `results` を抽出する設計が安全です。

### 6.2 入力件数と出力件数が不一致

**症状**: 入力10件に対し出力9件、または件数バラバラ

**原因**:
- LLMがレコードをスキップした
- max_tokens 不足で途中切断

**対処**:
- max_tokens を増やす（推奨 8000）
- バッチサイズを下げる（10件 → 5件）
- システムプロンプトの「出力配列の長さは入力レコード数と必ず一致させること」が太字で見えるか確認

### 6.3 If/Else分岐が想定通り動かない

**症状**: ML を入力したのに LENS 用LLMが動く

**原因**:
- `product_type` の値の比較がうまくいっていない
- 大文字小文字の扱い

**対処**:
- If/Else ノードの条件: `product_type` `is` `ML`（完全一致）
- 値が `"ML"`（クォート付き文字列）か `ML`（プレーン）か Dify上で確認
- Dify のバージョンによってはノード変数の型推論が異なる

### 6.4 records_json のサイズオーバー

**症状**: 「Input is too long」「max_tokens exceeded」

**原因**: バッチサイズ過大、レコードのテキスト長が大きい

**対処**:
- Python側でバッチサイズを下げる（10 → 5）
- レコードの `internal_1`, `internal_2` が極端に長い場合は preprocess で切り詰め

### 6.5 出力JSONが壊れる（途中で切れる）

**症状**: `[{"repair_id": "R001", "sub_id": 1, ... "evidence": "途中で` のように切れる

**原因**: max_tokens 不足

**対処**:
- max_tokens を増やす（8000 → 12000）
- バッチサイズを下げる（10 → 5）
- evidence の文字数指示を強める（プロンプトで「30文字以内」を再確認）

### 6.6 LLMが日本語で誤ったコードを生成

**症状**: `"failure_category_code": "電源不良"` （コード名で返してしまう）

**原因**: プロンプトで「コードを返せ」が伝わっていない

**対処**:
- システムプロンプトの故障コード一覧で `M001 (電源不良)` の表記を確認
- 出力フォーマット例で `"failure_category_code": "M001"` のようにコード形式を強調
- フューショット例を1件追加検討（ただしプロンプト肥大化）

---

## 7. 運用ガイド

### 7.1 YAML更新時の反映フロー

故障分類コード体系（YAML）を更新した場合の手順:

```
1. config/classification_codes.yaml を編集

2. プロジェクトルートで以下を実行:
   PYTHONPATH=src python3 -m prompt_builder

3. outputs/dify_prompts/ の以下が更新される:
   - system_prompt_ML.txt
   - system_prompt_LENS.txt

4. Dify管理画面で各LLMノードのSystemプロンプトを上書き
   （user_prompt_template.txt は通常更新不要）

5. ワークフローを「公開」して反映

6. テストペイロードで動作確認
   （docs/dify/test_payloads/ 配下を使用）
```

### 7.2 プロンプト調整のコツ

LLMの分類精度に問題がある場合:

| 症状 | 対処 |
|---|---|
| OTHER 比率が高い | コード体系の見直し、新しいカテゴリの追加検討 |
| UNK 比率が高い | コメントが情報不足。バッチサイズを下げる、入力にcontextを追加 |
| 同じコードが多発 | カテゴリの description を具体化、紛らわしい区別ルールを追加 |
| ユーザ視点に修理者情報が混入 | システムプロンプトのタスク1の「完全に無視」を強調 |
| confidence 全体的に低い | LLMモデルを上位に変更（gpt-4o → gpt-4-turbo） |

### 7.3 コスト見積もり

GPT-4o の場合（2025年時点の参考価格）:

- Input: 約 $2.50 / 1M tokens
- Output: 約 $10.00 / 1M tokens

1バッチ（10件）の概算:
- Input: system 7K + user 1.5K = 8.5K tokens
- Output: 約 3K tokens（10件分の分類結果）
- 1バッチコスト: 約 $0.05（≈ 7円）

1000件処理時:
- 100バッチ × $0.05 = $5（≈ 700円）

実際は試行錯誤で2-3倍かかる想定。最新の価格は OpenAI の料金表を確認。

### 7.4 セキュリティ留意事項

- APIキー（`app-xxx...`）はソースコードに直接書かない（環境変数 or .env）
- セルフホスティング Dify のホストURLも .env で管理推奨
- Dify管理画面のアクセスはVPN/社内ネットワーク経由を推奨

---

## 8. Python クライアント側の実装

`src/dify_client.py` を別途実装。詳細は本ドキュメント完成後の次タスク。

最低限の実装イメージ:

```python
import aiohttp
import asyncio
import json

async def call_dify_workflow(records: list[dict], product_type: str,
                              api_key: str, base_url: str) -> list[dict]:
    payload = {
        "inputs": {
            "records_json": json.dumps(records, ensure_ascii=False),
            "n_records": len(records),
            "product_type": product_type,
        },
        "response_mode": "blocking",
        "user": "python_client",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base_url}/v1/workflows/run",
            json=payload,
            headers=headers,
            timeout=120,
        ) as resp:
            data = await resp.json()
            result_str = data["data"]["outputs"]["result"]
            return json.loads(result_str)
```

---

## 9. 次のアクション

- [x] Dify モデルプロバイダー（OpenAI）登録
- [x] アプリケーション作成
- [x] 開始ノード入力変数設定
- [x] If/Else ノード追加
- [x] ML/LENS LLMノード追加とプロンプト貼り付け
- [x] 変数アサイナー・終了ノード設定
- [x] 管理画面でテスト実行
- [x] APIキー取得
- [x] curl で API動作確認
- [ ] `dify_client.py` 実装
- [ ] パイロット実行（20-30件で動作確認）
- [ ] 本処理（数百〜数千件）
