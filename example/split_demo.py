"""
split_demo.py
=============
split モジュールの使用例デモ。
01_split_validation.ipynb に移植しやすい構成。

実行: cd repair_failure_classifier && python3 examples/split_demo.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

# プロジェクトルートからの相対パス想定
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from split import ColumnMapping, SplitConfig, split_records

# ロギング設定（Notebookでも見やすく）
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
)


def make_demo_data() -> pd.DataFrame:
    """デモ用の修理データを生成（実運用に近いミックス）。"""
    return pd.DataFrame([
        # === 通常レコード（番号なし） ===
        {
            "repair_id": "R001",
            "product_type": "ML",
            "user_comment": "冬場の屋外撮影でピントが合わなくなります",
            "repair_comment": "低温環境下でのAF動作不良を確認、AFユニット交換にて復旧",
            "internal_1": "同症状増加傾向",
            "internal_2": "",
        },
        # === きれいに分割できるケース ===
        {
            "repair_id": "R002",
            "product_type": "ML",
            "user_comment": "①AFが効きません ②シャッターも切れません",
            "repair_comment": "①AFユニット交換 ②シャッターブロック交換",
            "internal_1": "保証期間内",
            "internal_2": "顧客優先",
        },
        # === 3分割 ===
        {
            "repair_id": "R003",
            "product_type": "LENS",
            "user_comment": "①ズームが固い ②AF迷う ③外装にキズ",
            "repair_comment": "①ズームリング洗浄調整 ②AF調整 ③外装交換",
            "internal_1": "",
            "internal_2": "",
        },
        # === 番号不一致（分割しない） ===
        {
            "repair_id": "R004",
            "product_type": "ML",
            "user_comment": "①ピント不良 ②電源不安定",
            "repair_comment": "①AF調整実施",  # 修理者は1つしか書いていない
            "internal_1": "",
            "internal_2": "",
        },
        # === 番号1つのみ（分割しない） ===
        {
            "repair_id": "R005",
            "product_type": "ML",
            "user_comment": "①AF不良",
            "repair_comment": "①AF調整",
            "internal_1": "",
            "internal_2": "",
        },
        # === フォールバックパターン（半角番号） ===
        {
            "repair_id": "R006",
            "product_type": "LENS",
            "user_comment": "(1)AF動作不良 (2)異音",
            "repair_comment": "(1)AF調整 (2)レンズユニット内清掃",
            "internal_1": "",
            "internal_2": "",
        },
        # === 空レコード ===
        {
            "repair_id": "R007",
            "product_type": "ML",
            "user_comment": "",
            "repair_comment": "",
            "internal_1": "",
            "internal_2": "",
        },
        # === 異常分割数 ===
        {
            "repair_id": "R008",
            "product_type": "ML",
            "user_comment": "①AF ②電源 ③シャッター ④LCD ⑤画像 ⑥音声 ⑦動画 ⑧無線",
            "repair_comment": "①AF調整 ②電池清掃 ③SU交換 ④LCD交換 ⑤センサー清掃 ⑥マイク交換 ⑦FW更新 ⑧基板交換",
            "internal_1": "",
            "internal_2": "",
        },
        # === 前後のコメント ===
        {
            "repair_id": "R009",
            "product_type": "ML",
            "user_comment": "【前回履歴】H111①AF ②電源 ▪️同時預かり",
            "repair_comment": "①AF調整 ②電池清掃 ",
            "internal_1": "",
            "internal_2": "",
        },
        {
            "repair_id": "R010",
            "product_type": "ML",
            "user_comment": "【前回履歴】H111①AF ②電源 ※同時預かり",
            "repair_comment": "①AF調整 ②電池清掃 ",
            "internal_1": "",
            "internal_2": "",
        },
        {
            "repair_id": "R011",
            "product_type": "ML",
            "user_comment": "【前回履歴】H111①AF ②電源 ※同時預かり",
            "repair_comment": "①AF調整 ②電池清掃 ※同時預かり",
            "internal_1": "",
            "internal_2": "",
        },
        {
            "repair_id": "R012",
            "product_type": "ML",
            "user_comment": "フリーズ・エラー70・ブラックアウト①エラー ②マウント板ばね破損",
            "repair_comment": "①現象確認できず ②破損確認",
            "internal_1": "",
            "internal_2": "",
        },
        {
            "repair_id": "R013",
            "product_type": "ML",
            "user_comment": "【ショック品】【オーバホール】①エラー ②マウント板ばね破損",
            "repair_comment": "オーバーホールを実施いたしました ①現象確認できず ②破損確認",
            "internal_1": "",
            "internal_2": "",
        },

        # === 指摘外 ===
        {
            "repair_id": "R014",
            "product_type": "ML",
            "user_comment": "①AF 【ご指摘外の現象】②電源 【追加ご指摘】③シャッター 【修理時に発見しました】④外装ラバー劣化",
            "repair_comment": "①AF調整 ②電池清掃 ③シャッター交換 ④外装ラバー交換",
            "internal_1": "",
            "internal_2": "",
        },
        # === 連続 ===
        {
            "repair_id": "R015",
            "product_type": "ML",
            "user_comment": "①AF ②電源",
            "repair_comment": "①②ご指摘の現象確認",
            "internal_1": "",
            "internal_2": "",
        },
        {
            "repair_id": "R016",
            "product_type": "ML",
            "user_comment": "①レンズ接点 ②電源 ③シャッター ④LCD ⑤画像 ⑥音声 ⑦動画",
            "repair_comment": "①②レンズ側にて対応します。カメラには異常なし ③④⑤⑥⑦ご指摘外の現象",
            "internal_1": "",
            "internal_2": "",
        },
        # === レンズ対応 ===
        {
            "repair_id": "R017",
            "product_type": "ML",
            "user_comment": "①レンズ接点 ②電源",
            "repair_comment": "①②レンズ側にて対応します。カメラには異常なし ",
            "internal_1": "",
            "internal_2": "",
        },
        # === 番号割り込み ===
        {
            "repair_id": "R018",
            "product_type": "ML",
            "user_comment": "メンテを依頼したが、①AF ②電源 ③雨の中で撮影",
            "repair_comment": "①②ご指摘の現象確認できませんでしたが③により発生した可能性が考えられる。③内部の腐食確認",
            "internal_1": "",
            "internal_2": "",
        },
        

    ])


def main():
    print("=" * 60)
    print("1. デモデータ生成")
    print("=" * 60)
    df = make_demo_data()
    print(f"入力レコード数: {len(df)}")
    print(df[["repair_id", "product_type", "user_comment", "repair_comment"]].to_string(index=False))
    print()

    print("=" * 60)
    print("2. 分割処理（デフォルト設定）")
    print("=" * 60)
    config = SplitConfig()
    split_df, report = split_records(df, config)
    print(report.summary())
    print()

    print("=" * 60)
    print("3. 出力DataFrame")
    print("=" * 60)
    # 表示用に整形
    show_cols = ["repair_id", "sub_id", "product_type",
                 "user_text", "repair_text", "split_info"]
    print(split_df[show_cols].to_string(index=False))
    print()

    print("=" * 60)
    print("4. 警告ログ")
    print("=" * 60)
    if report.warnings:
        for w in report.warnings:
            print(f"  - {w}")
    else:
        print("  （警告なし）")
    print()

    print("=" * 60)
    print("5. 分割パターン別の集計")
    print("=" * 60)
    pattern_summary = split_df.groupby("split_info").size().reset_index(name="count")
    print(pattern_summary.to_string(index=False))
    print()

    print("=" * 60)
    print("6. カスタムカラム名でのデモ")
    print("=" * 60)
    df_custom = pd.DataFrame([
        {
            "id": "X001",
            "category": "ML",
            "user_text_jp": "①AF不良 ②電源不良",
            "repair_text_jp": "①AF調整 ②電池清掃",
            "memo1": "",
            "memo2": "",
        }
    ])
    custom_config = SplitConfig(
        columns=ColumnMapping(
            repair_id="id",
            product_type="category",
            user_comment="user_text_jp",
            repair_comment="repair_text_jp",
            internal_1="memo1",
            internal_2="memo2",
        )
    )
    custom_df, custom_report = split_records(df_custom, custom_config)
    print("カスタムカラム名で分割成功:")
    print(custom_df[["repair_id", "sub_id", "user_text", "repair_text"]].to_string(index=False))
    split_df.to_csv('test_demo.csv')

if __name__ == "__main__":
    main()
