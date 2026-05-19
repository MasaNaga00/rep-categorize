"""
codes_loader_demo.py
====================
codes_loader モジュールの使用例デモ。
Notebookに貼り付けても動く構成。
"""

from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートからの相対パス想定
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from codes_loader import (
    CodeBookLoadError,
    DescriptionStatus,
    ProductType,
    RecordType,
    Responsibility,
    load_codes,
)


def main():
    yaml_path = Path(__file__).parent.parent / "config" / "classification_codes.yaml"

    # === 1. ロード ===
    print("=" * 60)
    print("1. YAMLロード")
    print("=" * 60)
    try:
        codebook = load_codes(yaml_path)
        print(f"✅ ロード成功: バージョン {codebook.meta.version}")
    except CodeBookLoadError as e:
        print(f"❌ ロード失敗: {e}")
        return

    # === 2. メタ情報 ===
    print()
    print("=" * 60)
    print("2. メタ情報")
    print("=" * 60)
    print(f"バージョン: {codebook.meta.version}")
    print(f"ソース: {codebook.meta.source}")
    print(f"作成日: {codebook.meta.created_at}")
    if codebook.meta.changelog:
        print(f"変更履歴: {len(codebook.meta.changelog)}件")
        for entry in codebook.meta.changelog:
            print(f"  - v{entry.version} ({entry.date})")

    # === 3. 製品別の故障コード ===
    print()
    print("=" * 60)
    print("3. 製品別の故障コード")
    print("=" * 60)
    for product in [ProductType.ML, ProductType.LENS]:
        codes = codebook.get_failure_codes_for_product(product)
        print(f"\n[{product.value}] {len(codes)}項目")

        # record_type別に集計
        by_type: dict[str, int] = {}
        for fc in codes.values():
            key = fc.record_type.value
            by_type[key] = by_type.get(key, 0) + 1
        print(f"  record_type分布: {by_type}")

        # 特殊コード
        specials = [(c, fc.name) for c, fc in codes.items() if fc.is_special]
        print(f"  特殊コード: {specials}")

        # responsibility属性付き
        resp = [
            (c, fc.name, fc.responsibility.value)
            for c, fc in codes.items()
            if fc.responsibility is not None
        ]
        if resp:
            print(f"  responsibility属性付き:")
            for c, name, r in resp:
                print(f"    {c}: {name} → {r}")

    # === 4. ヘルパー関数の利用例 ===
    print()
    print("=" * 60)
    print("4. ヘルパー関数の利用例")
    print("=" * 60)

    # ケース: LLM出力の検証
    test_outputs = [
        ("M012", "ML"),   # 有効、メーカー責任
        ("M013", "ML"),   # 有効、ユーザ責任
        ("M042", "ML"),   # 有効、サービスレコード
        ("M999", "ML"),   # 無効
        ("L001", "LENS"), # 有効
        ("M001", "LENS"), # 製品違い
    ]

    print(f"{'コード':<6} {'製品':<6} {'有効':<6} {'メーカー責任':<12} {'サービス':<8}")
    print("-" * 50)
    for code, product in test_outputs:
        valid = codebook.is_valid_failure_code(code, product)
        is_mfr = codebook.is_manufacturer_responsibility(code, product)
        is_svc = codebook.is_service_record(code, product)
        print(f"{code:<6} {product:<6} {str(valid):<6} {str(is_mfr):<12} {str(is_svc):<8}")

    # === 5. 環境要因と再現状況 ===
    print()
    print("=" * 60)
    print("5. 環境要因と再現状況")
    print("=" * 60)
    print(f"\n環境要因 ({len(codebook.environment_factors)}項目):")
    for key, ef in codebook.environment_factors.items():
        special_mark = " [特殊]" if ef.is_special else ""
        print(f"  {key}: {ef.name}{special_mark}")

    print(f"\n再現状況 ({len(codebook.reproduction_statuses)}項目):")
    for key, rs in codebook.reproduction_statuses.items():
        print(f"  {key}: {rs.name}")

    # === 6. 分類ルール ===
    print()
    print("=" * 60)
    print("6. 分類ルール（プロンプト埋め込み用）")
    print("=" * 60)
    if codebook.classification_rules:
        rules = codebook.classification_rules
        print(f"general: {len(rules.general)}件")
        print(f"reproduction_handling: {len(rules.reproduction_handling)}件")
        print(f"service_records: {len(rules.service_records)}件")
        print(f"responsibility_aware: {len(rules.responsibility_aware)}件")
        if rules.dual_perspective:
            print(f"dual_perspective: 設定あり")
            print(f"  user_perspective.input_scope: "
                  f"{rules.dual_perspective.user_perspective.get('input_scope')}")
            print(f"  repair_perspective.input_scope: "
                  f"{rules.dual_perspective.repair_perspective.get('input_scope')}")


if __name__ == "__main__":
    main()
