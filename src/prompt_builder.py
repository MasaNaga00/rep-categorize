"""
prompt_builder.py
=================
Dify ワークフロー用のプロンプト・テンプレートを生成するツール。

役割（v2 で変更）:
    - 旧: 実行時に毎回プロンプトを生成して LLM API に送る
    - 新: Dify のシステムプロンプト・ユーザプロンプトに**貼り付ける**
          ためのテキストファイルを生成する CLI ツール

設計判断:
    - プロンプトは Dify ワークフロー内に固定（更新頻度が低い前提）
    - ML/LENS の分岐は Dify ワークフローの if/else で実装
    - レコードは JSON 形式で渡す（取り違えリスク低減）
    - responsibility はプロンプト非掲載（Python側で派生指標化）

使い方:
    # CLI として:
    cd repair_failure_classifier
    python -m src.prompt_builder            # 全成果物を outputs/dify_prompts/ に出力
    python -m src.prompt_builder --product ML
    python -m src.prompt_builder --output /custom/path

    # ライブラリとして:
    from codes_loader import load_codes
    from prompt_builder import PromptBuilder

    codebook = load_codes("config/classification_codes.yaml")
    builder = PromptBuilder(codebook)
    system_ml = builder.build_system_prompt("ML")
    user_template = builder.build_user_message_template()

    # 実テスト用にレコードを当て込む:
    user_msg = builder.build_user_message_for_records(records)

生成される成果物:
    outputs/dify_prompts/
      ├── system_prompt_ML.txt    # Dify ML分岐用 LLMノードの「System」に貼り付け
      ├── system_prompt_LENS.txt  # Dify LENS分岐用 LLMノードの「System」に貼り付け
      ├── user_prompt_template.txt # Dify LLMノードの「User」に貼り付け（共通）
      └── README.md                # Dify ワークフロー設計手順
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from codes_loader import (
    CodeBook,
    EnvironmentFactor,
    FailureCategory,
    ProductType,
    RecordType,
    ReproductionStatus,
    load_codes,
)


# =============================================================================
# 例外
# =============================================================================

class PromptBuilderError(Exception):
    """プロンプト生成失敗時の例外。"""
    pass


# =============================================================================
# 製品種別の表示名
# =============================================================================

PRODUCT_TYPE_NAMES = {
    "ML": "ミラーレスカメラ",
    "LENS": "交換レンズ",
}


# =============================================================================
# 必須レコードキー
# =============================================================================

REQUIRED_RECORD_KEYS = {
    "repair_id", "sub_id",
    "user_text", "user_context",
    "repair_text", "repair_context",
    "internal_1", "internal_2",
}

# JSON出力時のキー順序（LLMが読みやすい順）
RECORD_KEY_ORDER = [
    "repair_id", "sub_id",
    "user_text", "user_context",
    "repair_text", "repair_context",
    "internal_1", "internal_2",
]

# Dify がユーザプロンプト内で参照する変数名（プレースホルダ）
DIFY_RECORDS_VARIABLE = "records_json"
DIFY_RECORDS_COUNT_VARIABLE = "n_records"


# =============================================================================
# テンプレート用データ整形
# =============================================================================

def _serialize_failure_codes(
    codes: dict[str, FailureCategory],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """FailureCategory辞書を、テンプレート用のリストと特殊コード辞書に分解する。"""
    other_code: str | None = None
    unk_code: str | None = None

    normal_codes: list[tuple[str, FailureCategory]] = []
    other_specials: list[tuple[str, FailureCategory]] = []
    unk_specials: list[tuple[str, FailureCategory]] = []

    for code, fc in codes.items():
        if fc.is_special:
            if fc.record_type == RecordType.UNKNOWN:
                unk_specials.append((code, fc))
                unk_code = code
            else:
                other_specials.append((code, fc))
                other_code = code
        else:
            normal_codes.append((code, fc))

    if other_code is None or unk_code is None:
        raise PromptBuilderError(
            f"特殊コード（OTHER/UNK）が特定できません: "
            f"other={other_code}, unk={unk_code}"
        )

    failure_list: list[dict[str, Any]] = []
    for code, fc in normal_codes + other_specials + unk_specials:
        failure_list.append({
            "code": code,
            "name": fc.name,
            "description": fc.description,
            "record_type": fc.record_type.value,
            "decision_rule": fc.decision_rule,
        })

    return failure_list, {"other_code": other_code, "unk_code": unk_code}


def _serialize_env_factors(
    env_factors: dict[str, EnvironmentFactor],
) -> list[dict[str, Any]]:
    """環境要因をテンプレート用リストに整形。通常項目を先、特殊（none/unknown）を後ろに。"""
    normal_keys = [k for k, v in env_factors.items() if not v.is_special]
    special_keys = [k for k, v in env_factors.items() if v.is_special]

    result = []
    for key in normal_keys + special_keys:
        ef = env_factors[key]
        result.append({
            "key": key,
            "name": ef.name,
            "description": ef.description,
            "keywords": ef.keywords,
            "is_special": ef.is_special,
        })
    return result


def _serialize_reproduction_statuses(
    statuses: dict[str, ReproductionStatus],
) -> list[dict[str, Any]]:
    """再現状況をテンプレート用リストに整形。順序固定。"""
    order = ["reproduced", "not_reproduced", "partial", "not_attempted"]
    result = []
    for key in order:
        if key in statuses:
            rs = statuses[key]
            result.append({
                "key": key,
                "name": rs.name,
                "description": rs.description,
                "keywords": rs.keywords,
            })
    # 順序外のキーを末尾に
    for key, rs in statuses.items():
        if key not in order:
            result.append({
                "key": key,
                "name": rs.name,
                "description": rs.description,
                "keywords": rs.keywords,
            })
    return result


# =============================================================================
# レコードバリデーション
# =============================================================================

def _validate_record(record: dict[str, Any], idx: int) -> None:
    """1レコードに必須キーが揃っているかチェック。"""
    missing = REQUIRED_RECORD_KEYS - set(record.keys())
    if missing:
        raise PromptBuilderError(
            f"レコード [{idx}] に必須キーが不足: {missing}"
        )


def _records_to_json(records: list[dict[str, Any]]) -> str:
    """
    レコードリストを Dify/LLM 向けの JSON 文字列に変換。

    必須キーのみを抽出し、固定順序で出力。日本語は ensure_ascii=False。
    """
    cleaned = []
    for idx, r in enumerate(records):
        _validate_record(r, idx)
        # 順序を固定
        cleaned.append({k: r[k] for k in RECORD_KEY_ORDER})

    return json.dumps(cleaned, ensure_ascii=False, indent=2)


# =============================================================================
# PromptBuilder 本体
# =============================================================================

@dataclass
class PromptBuilder:
    """
    プロンプト生成器。CodeBook と Jinja2 環境を保持する。

    Dify のシステムプロンプト/ユーザプロンプトに貼り付ける用のテキストを生成する。
    """

    codebook: CodeBook
    template_dir: Path | str = "config/prompts"
    system_template_name: str = "system_prompt.j2"
    user_template_name: str = "user_message.j2"

    def __post_init__(self) -> None:
        template_dir = Path(self.template_dir)
        if not template_dir.exists():
            raise PromptBuilderError(
                f"テンプレートディレクトリが見つかりません: {template_dir}"
            )

        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(disabled_extensions=("j2",)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

        try:
            self._system_template = self._env.get_template(self.system_template_name)
            self._user_template = self._env.get_template(self.user_template_name)
        except Exception as e:
            raise PromptBuilderError(f"テンプレート読み込み失敗: {e}") from e

    # =========================================================================
    # システムプロンプト
    # =========================================================================

    def build_system_prompt(self, product_type: str | ProductType) -> str:
        """
        指定製品種別のシステムプロンプトを生成（Dify貼り付け用）。

        Args:
            product_type: "ML" または "LENS"

        Returns:
            完成したシステムプロンプト文字列
        """
        pt = product_type.value if isinstance(product_type, ProductType) else product_type

        if pt not in PRODUCT_TYPE_NAMES:
            raise PromptBuilderError(f"未知の製品種別: {pt}")

        codes = self.codebook.get_failure_codes_for_product(pt)
        failure_codes, special_codes = _serialize_failure_codes(codes)
        env_factors = _serialize_env_factors(self.codebook.environment_factors)
        repro_statuses = _serialize_reproduction_statuses(
            self.codebook.reproduction_statuses
        )

        return self._system_template.render(
            product_type=pt,
            product_type_name=PRODUCT_TYPE_NAMES[pt],
            failure_codes=failure_codes,
            special_codes=special_codes,
            env_factors=env_factors,
            reproduction_statuses=repro_statuses,
        )

    # =========================================================================
    # ユーザプロンプトテンプレート（Dify貼り付け用）
    # =========================================================================

    def build_user_message_template(self) -> str:
        """
        Dify のユーザプロンプトに貼り付ける**テンプレート**を生成。

        Dify が変数を埋め込むためのプレースホルダ（{{records_json}} 等）が
        含まれた状態のテキストを返す。

        Dify ワークフロー側で:
          - records_json: JSON文字列の入力変数として定義
          - n_records: 件数の入力変数として定義
        を設定する必要がある。

        Returns:
            プレースホルダ入りのユーザプロンプトテンプレート文字列
        """
        # Dify 用のプレースホルダ表記（Dify は {{variable}} 形式）
        # Jinja2 のレンダリング時に変数として渡し、
        # 結果として文字列に "{{records_json}}" が含まれる状態にする
        return self._user_template.render(
            records_json="{{" + DIFY_RECORDS_VARIABLE + "}}",
            n_records="{{" + DIFY_RECORDS_COUNT_VARIABLE + "}}",
        )

    # =========================================================================
    # 実データ用ユーザメッセージ生成（テスト・検証用）
    # =========================================================================

    def build_user_message_for_records(
        self, records: list[dict[str, Any]],
    ) -> str:
        """
        実際のレコードを埋め込んだユーザメッセージを生成（テスト・検証用）。

        Dify を介さず直接 LLM API でテストしたい場合や、
        プロンプトの最終形を確認するのに使用。

        Args:
            records: 分類対象レコードのリスト（split.py の出力形式）

        Returns:
            完成したユーザメッセージ文字列
        """
        if not records:
            raise PromptBuilderError("レコードが空です")

        records_json = _records_to_json(records)

        return self._user_template.render(
            records_json=records_json,
            n_records=len(records),
        )

    # =========================================================================
    # 補助: バッチ分割
    # =========================================================================

    @staticmethod
    def split_into_batches(
        records: list[dict[str, Any]], batch_size: int = 10,
    ) -> list[list[dict[str, Any]]]:
        """レコードリストを指定サイズのバッチに分割。"""
        if batch_size <= 0:
            raise ValueError("batch_size は1以上")
        return [
            records[i:i + batch_size]
            for i in range(0, len(records), batch_size)
        ]


# =============================================================================
# CLI: Dify貼り付け用ファイル生成
# =============================================================================

DIFY_SETUP_README = """\
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
"""


def _generate_all_prompts(
    codebook: CodeBook,
    template_dir: Path,
    output_dir: Path,
    products: list[str] | None = None,
) -> dict[str, Path]:
    """
    全成果物を生成して指定ディレクトリに保存。

    Args:
        codebook: コード体系
        template_dir: Jinja2 テンプレートディレクトリ
        output_dir: 出力先ディレクトリ
        products: 対象製品種別（None で全て）

    Returns:
        {成果物名: ファイルパス} の辞書
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    builder = PromptBuilder(codebook=codebook, template_dir=template_dir)

    if products is None:
        products = ["ML", "LENS"]

    generated: dict[str, Path] = {}

    # 各製品種別のシステムプロンプト
    for pt in products:
        path = output_dir / f"system_prompt_{pt}.txt"
        path.write_text(
            builder.build_system_prompt(pt),
            encoding="utf-8",
        )
        generated[f"system_{pt}"] = path

    # ユーザプロンプトテンプレート（製品共通）
    user_path = output_dir / "user_prompt_template.txt"
    user_path.write_text(
        builder.build_user_message_template(),
        encoding="utf-8",
    )
    generated["user_template"] = user_path

    # README
    readme_path = output_dir / "README.md"
    readme_path.write_text(DIFY_SETUP_README, encoding="utf-8")
    generated["readme"] = readme_path

    return generated


def main(argv: list[str] | None = None) -> int:
    """CLIエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="Dify貼り付け用プロンプトファイルを生成",
    )
    parser.add_argument(
        "--codes",
        type=Path,
        default=Path("config/classification_codes.yaml"),
        help="コード体系YAMLのパス",
    )
    parser.add_argument(
        "--templates",
        type=Path,
        default=Path("config/prompts"),
        help="Jinja2テンプレートディレクトリ",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/dify_prompts"),
        help="出力先ディレクトリ",
    )
    parser.add_argument(
        "--product",
        choices=["ML", "LENS"],
        action="append",
        help="対象製品種別を限定（複数指定可、未指定で全製品）",
    )
    args = parser.parse_args(argv)

    print(f"Loading codes from: {args.codes}")
    codebook = load_codes(args.codes)
    print(f"  version: {codebook.meta.version}")

    products = args.product  # None なら全製品
    print(f"Generating prompts for: {products if products else 'ML, LENS'}")
    print(f"Output directory: {args.output}")

    generated = _generate_all_prompts(
        codebook=codebook,
        template_dir=args.templates,
        output_dir=args.output,
        products=products,
    )

    print()
    print("Generated files:")
    for name, path in generated.items():
        size = path.stat().st_size
        print(f"  [{name}] {path} ({size:,} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
