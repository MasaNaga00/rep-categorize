"""
prompt_builder_demo.py
======================
prompt_builder（v2: Dify貼り付け用ツール）のデモ。

実行手順:
    cd repair_failure_classifier
    python3 examples/prompt_builder_demo.py

このデモが示すフロー:
    1. CLI で Dify貼り付け用ファイルを生成
    2. テスト用に実レコードを使ってプロンプト最終形を確認
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from codes_loader import load_codes
from prompt_builder import PromptBuilder, _generate_all_prompts
from split import split_records


def make_demo_data() -> pd.DataFrame:
    """実データ風のデモデータ。"""
    return pd.DataFrame([
        {
            "repair_id": "R001", "product_type": "ML",
            "user_comment": "冬場の屋外撮影でピントが合わなくなります",
            "repair_comment": "低温環境下でのAF動作不良を確認、AFユニット交換にて復旧",
            "internal_1": "同症状増加傾向", "internal_2": "",
        },
        {
            "repair_id": "R009", "product_type": "ML",
            "user_comment": "【前回履歴】H111①AF ②電源 ▪️同時預かり",
            "repair_comment": "①AF調整 ②電池清掃",
            "internal_1": "", "internal_2": "",
        },
        {
            "repair_id": "R012", "product_type": "ML",
            "user_comment": "フリーズ・エラー70・ブラックアウト①エラー ②マウント板ばね破損",
            "repair_comment": "①現象確認できず ②破損確認",
            "internal_1": "", "internal_2": "",
        },
    ])


def main():
    print("=" * 70)
    print("Step 1: Dify貼り付け用ファイルを生成")
    print("=" * 70)
    codebook = load_codes(PROJECT_ROOT / "config" / "classification_codes.yaml")
    output_dir = PROJECT_ROOT / "outputs" / "dify_prompts"

    generated = _generate_all_prompts(
        codebook=codebook,
        template_dir=PROJECT_ROOT / "config" / "prompts",
        output_dir=output_dir,
    )

    print(f"出力先: {output_dir}")
    for name, path in generated.items():
        print(f"  [{name}] {path.name}: {path.stat().st_size:,} bytes")
    print()
    print("→ これらのファイルを Dify ワークフロー画面にコピペ:")
    print("  - system_prompt_ML.txt   → ML分岐 LLMノードの System")
    print("  - system_prompt_LENS.txt → LENS分岐 LLMノードの System")
    print("  - user_prompt_template.txt → 両LLMノードの User（共通）")
    print()

    # ----------------------------------------------------------------------
    print("=" * 70)
    print("Step 2: ローカル検証用にレコードを埋め込んだ最終形を確認")
    print("=" * 70)

    raw_df = make_demo_data()
    split_df, _ = split_records(raw_df)
    records = split_df.to_dict("records")

    builder = PromptBuilder(
        codebook=codebook,
        template_dir=PROJECT_ROOT / "config" / "prompts",
    )

    # 製品種別ごとに処理
    by_product: dict[str, list] = {}
    for r in records:
        by_product.setdefault(r["product_type"], []).append(r)

    for product_type, recs in by_product.items():
        print(f"\n[{product_type}] {len(recs)}レコード")

        # Dify貼り付け前提なので、Pythonからは送信時の payload を構築する形
        sys_prompt = builder.build_system_prompt(product_type)
        user_msg = builder.build_user_message_for_records(recs)

        print(f"  system prompt: {len(sys_prompt):,} 文字")
        print(f"  user message:  {len(user_msg):,} 文字")
        print(f"  合計: {len(sys_prompt) + len(user_msg):,} 文字")
    print()

    # ----------------------------------------------------------------------
    print("=" * 70)
    print("Step 3: ML向けユーザメッセージ（最終形プレビュー）")
    print("=" * 70)
    ml_records = by_product.get("ML", [])
    if ml_records:
        user_msg = builder.build_user_message_for_records(ml_records)
        print(user_msg)
    print()

    # ----------------------------------------------------------------------
    print("=" * 70)
    print("Step 4: Difyワークフロー実行時の payload 例")
    print("=" * 70)
    import json

    if ml_records:
        # split.py 出力 → JSON文字列
        records_json_str = builder.build_user_message_for_records(ml_records)
        # JSON部分だけ抽出（実運用ではこの抽出は不要、records自体をJSONに変換するだけ）
        print("Difyワークフロー実行時に渡す入力変数:")
        print()

        # 必須キーのみで JSON 化（split_info 等は除外）
        from prompt_builder import RECORD_KEY_ORDER
        clean_records = [
            {k: r[k] for k in RECORD_KEY_ORDER}
            for r in ml_records
        ]
        payload = {
            "records_json": json.dumps(clean_records, ensure_ascii=False, indent=2),
            "n_records": len(ml_records),
            "product_type": "ML",
        }
        # records_json は長いので冒頭だけ表示
        preview = payload.copy()
        preview["records_json"] = preview["records_json"][:200] + "..."
        print(json.dumps(preview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
