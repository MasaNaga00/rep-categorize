"""
test_split.py
=============
split.py（v2: 4ゾーン対応）の単体テスト。

カバー範囲:
    - 通常分割
    - 番号不一致、番号1つだけ、空レコード
    - 重複マーカー（v2新規）
    - 連続マーカーグループ展開（v2新規）
    - preamble/postamble保持（v2新規）
    - ユーザ/修理者の【】扱い差分（v2新規）
    - カラムマッピング、カスタム設定
"""

from __future__ import annotations

import pandas as pd
import pytest

from split import (
    OUTPUT_COLUMNS,
    ColumnMapping,
    SplitConfig,
    SplitReport,
    split_records,
)


# =============================================================================
# 基本ケース
# =============================================================================

class TestSplitRecordsBasic:
    def test_no_markers(self, sample_records_no_markers):
        df, report = split_records(sample_records_no_markers)
        assert len(df) == 2
        assert report.no_split_count == 2
        assert report.split_count == 0
        assert (df["sub_id"] == 1).all()
        assert (df["split_info"] == "no_markers").all()

    def test_no_markers_text_in_user_text(self, sample_records_no_markers):
        """マーカーなしの場合、user_text にコメント全文が入る。"""
        df, _ = split_records(sample_records_no_markers)
        r001 = df[df["repair_id"] == "R001"].iloc[0]
        assert r001["user_text"] == "冬場の屋外撮影でピントが合わなくなります"
        assert r001["user_context"] == ""  # マーカーなし時は context 空

    def test_well_formed_split(self, sample_records_well_formed_split):
        df, report = split_records(sample_records_well_formed_split)
        assert len(df) == 5  # R101: 2分割 + R102: 3分割
        assert report.split_count == 2

        r101 = df[df["repair_id"] == "R101"].sort_values("sub_id")
        assert len(r101) == 2
        assert r101.iloc[0]["user_text"] == "AFが効きません"
        assert r101.iloc[0]["repair_text"] == "AFユニット交換"
        assert r101.iloc[1]["user_text"] == "シャッターも切れません"

    def test_internal_duplicated(self, sample_records_well_formed_split):
        df, _ = split_records(sample_records_well_formed_split)
        r101 = df[df["repair_id"] == "R101"]
        assert (r101["internal_1"] == "顧客優先").all()


class TestOutputColumns:
    def test_all_output_columns_present(self, sample_records_well_formed_split):
        """出力DataFrameに全期待カラムが存在する。"""
        df, _ = split_records(sample_records_well_formed_split)
        for col in OUTPUT_COLUMNS:
            assert col in df.columns

    def test_empty_dataframe_has_columns(self):
        """空入力でもカラムが揃った空DataFrameを返す。"""
        df = pd.DataFrame(columns=[
            "repair_id", "product_type", "user_comment",
            "repair_comment", "internal_1", "internal_2",
        ])
        out_df, _ = split_records(df)
        for col in OUTPUT_COLUMNS:
            assert col in out_df.columns


# =============================================================================
# 番号不一致 / 1つだけ / 空 / フォールバック
# =============================================================================

class TestMarkerMismatch:
    def test_marker_mismatch_no_split(self, sample_records_marker_mismatch):
        df, report = split_records(sample_records_marker_mismatch)
        assert len(df) == 2  # 各1レコードずつ
        assert report.marker_mismatch_count == 2
        assert (df["split_info"] == "marker_mismatch").all()

    def test_mismatch_preserves_marker_zone(self, sample_records_marker_mismatch):
        """不一致時は marker_zone がそのまま user_text に残る。"""
        df, _ = split_records(sample_records_marker_mismatch)
        r201 = df[df["repair_id"] == "R201"].iloc[0]
        assert "①" in r201["user_text"]
        assert "②" in r201["user_text"]


class TestOneMarkerOnly:
    def test_one_marker_no_split(self, sample_records_one_marker_only):
        df, report = split_records(sample_records_one_marker_only)
        assert len(df) == 1
        assert report.no_split_count == 1


class TestEmptyRecords:
    def test_empty(self, sample_records_empty):
        df, report = split_records(sample_records_empty)
        assert len(df) == 2
        assert report.empty_record_count == 2
        assert (df["split_info"] == "empty").all()


class TestFallback:
    def test_fallback_warns(self, sample_records_fallback):
        df, report = split_records(sample_records_fallback)
        assert report.fallback_detected_count == 1
        assert report.split_count == 0
        assert any("fallback_detected" in w for w in report.warnings)

    def test_fallback_silent_mode(self, sample_records_fallback):
        config = SplitConfig(on_fallback_pattern="silent")
        df, report = split_records(sample_records_fallback, config)
        assert report.fallback_detected_count == 1
        assert not any("fallback" in w for w in report.warnings)


# =============================================================================
# Preamble / Bracket / Postamble の処理（v2新規）
# =============================================================================

class TestPreambleHandling:
    def test_user_brackets_dropped_freeform_kept(self, sample_records_with_preamble):
        """ユーザ: 【前回履歴】は除去、H111は preamble として保持。"""
        df, _ = split_records(sample_records_with_preamble)
        r009 = df[df["repair_id"] == "R009"].iloc[0]
        # context にH111が入る
        assert "H111" in r009["user_context"]
        # 【】は除去されている
        assert "【" not in r009["user_context"]
        assert "前回履歴" not in r009["user_context"]

    def test_user_freeform_preamble_kept(self, sample_records_with_preamble):
        """R012: フリーテキストpreambleは保持。"""
        df, _ = split_records(sample_records_with_preamble)
        r012_rows = df[df["repair_id"] == "R012"]
        # 全sub_idで context が同じ
        for _, row in r012_rows.iterrows():
            assert row["user_context"] == "フリーズ・エラー70・ブラックアウト"

    def test_repair_preamble_kept(self, sample_records_repair_preamble):
        """R013: 修理者のpreambleは保持。"""
        df, _ = split_records(sample_records_repair_preamble)
        r013_rows = df[df["repair_id"] == "R013"]
        for _, row in r013_rows.iterrows():
            assert "オーバーホール" in row["repair_context"]


class TestPostambleHandling:
    def test_user_postamble_extracted(self, sample_records_with_preamble):
        """R009: ▪️以降が postamble として分離される。"""
        df, _ = split_records(sample_records_with_preamble)
        r009_rows = df[df["repair_id"] == "R009"]
        for _, row in r009_rows.iterrows():
            assert "同時預かり" in row["user_postamble"]
            # marker_zone（user_text）には postamble が含まれない
            assert "同時預かり" not in row["user_text"]

    def test_postamble_asterisk(self, sample_records_postamble):
        """R011: ※以降が postamble。"""
        df, _ = split_records(sample_records_postamble)
        r011_rows = df[df["repair_id"] == "R011"]
        for _, row in r011_rows.iterrows():
            assert "同時預かり" in row["user_postamble"]
            assert "同時預かり" in row["repair_postamble"]


class TestMarkerZoneBracketCleaning:
    def test_user_brackets_in_marker_zone_removed(self, sample_records_user_brackets_in_marker_zone):
        """R014: 番号項目内の【】はユーザ側では除去される。"""
        df, _ = split_records(sample_records_user_brackets_in_marker_zone)
        r014 = df[df["repair_id"] == "R014"].sort_values("sub_id")
        assert len(r014) == 4
        # 各 user_text には【】が含まれない
        for _, row in r014.iterrows():
            assert "【" not in row["user_text"]
            assert "】" not in row["user_text"]
        # 期待される値
        assert r014.iloc[0]["user_text"] == "AF"
        assert r014.iloc[1]["user_text"] == "電源"
        assert r014.iloc[2]["user_text"] == "シャッター"
        assert r014.iloc[3]["user_text"] == "外装ラバー劣化"


# =============================================================================
# 連続マーカーグループ（v2 新規）
# =============================================================================

class TestGroupedMarkers:
    def test_pair_group_duplicates_chunk(self, sample_records_grouped_markers):
        """R015: ①②まとめ回答が両sub_idに複製される。"""
        df, report = split_records(sample_records_grouped_markers)
        # R015 と R017 → 計4レコード
        assert len(df) == 4
        assert report.split_count == 2

        r015 = df[df["repair_id"] == "R015"].sort_values("sub_id")
        # 両方の repair_text が同じ
        assert r015.iloc[0]["repair_text"] == "ご指摘の現象確認"
        assert r015.iloc[1]["repair_text"] == "ご指摘の現象確認"

    def test_full_group_all_same(self, sample_records_grouped_markers):
        """R017: ①②全部まとめ → 全sub_idで同じ修理者テキスト。"""
        df, _ = split_records(sample_records_grouped_markers)
        r017 = df[df["repair_id"] == "R017"]
        repair_texts = r017["repair_text"].unique()
        assert len(repair_texts) == 1
        assert "レンズ側" in repair_texts[0]


class TestMixedGrouped:
    def test_mixed_groups_distribute_correctly(self, sample_records_mixed_grouped):
        """R016: ①②と③④⑤⑥⑦の混在分布。"""
        df, _ = split_records(sample_records_mixed_grouped)
        r016 = df[df["repair_id"] == "R016"].sort_values("sub_id")
        assert len(r016) == 7

        # sub_id 1, 2 → "レンズ側にて対応"
        assert r016.iloc[0]["repair_text"] == "レンズ側にて対応"
        assert r016.iloc[1]["repair_text"] == "レンズ側にて対応"

        # sub_id 3-7 → "ご指摘外の現象"
        for i in range(2, 7):
            assert r016.iloc[i]["repair_text"] == "ご指摘外の現象"

        # ユーザ側は個別
        assert r016.iloc[0]["user_text"] == "レンズ接点"
        assert r016.iloc[1]["user_text"] == "電源"


# =============================================================================
# 重複マーカー（v2 新規）
# =============================================================================

class TestDuplicateMarkers:
    def test_duplicate_no_split(self, sample_records_duplicate_markers):
        """R018: 修理者文中の③重複で不分割。"""
        df, report = split_records(sample_records_duplicate_markers)
        assert len(df) == 1
        assert report.duplicate_marker_count == 1
        assert df.iloc[0]["split_info"] == "duplicate_markers"
        assert any("duplicate_markers" in w for w in report.warnings)

    def test_duplicate_preserves_full_text(self, sample_records_duplicate_markers):
        """重複時は元のmarker_zoneがそのまま入る。"""
        df, _ = split_records(sample_records_duplicate_markers)
        r018 = df.iloc[0]
        # 修理者の③が複数回出現するテキストがそのまま保持される
        assert "③" in r018["repair_text"]
        # メンテを依頼したが、 は preamble なので user_context に
        assert "メンテを依頼したが" in r018["user_context"]

    def test_duplicate_silent_mode(self, sample_records_duplicate_markers):
        config = SplitConfig(on_duplicate_markers="silent")
        df, report = split_records(sample_records_duplicate_markers, config)
        assert report.duplicate_marker_count == 1
        assert not any("duplicate" in w for w in report.warnings)


# =============================================================================
# 異常分割数
# =============================================================================

class TestAbnormalSplit:
    def test_threshold(self, sample_records_abnormal_split):
        df, report = split_records(sample_records_abnormal_split)
        assert len(df) == 8
        assert report.abnormal_split_count == 1

    def test_custom_threshold(self, sample_records_abnormal_split):
        config = SplitConfig(abnormal_split_threshold=10)
        _, report = split_records(sample_records_abnormal_split, config)
        assert report.abnormal_split_count == 0


# =============================================================================
# カラムマッピング
# =============================================================================

class TestColumnMapping:
    def test_custom_columns(self, sample_records_custom_columns):
        config = SplitConfig(columns=ColumnMapping(
            repair_id="id",
            product_type="category",
            user_comment="user_text_jp",
            repair_comment="repair_text_jp",
            internal_1="memo1",
            internal_2="memo2",
        ))
        df, _ = split_records(sample_records_custom_columns, config)
        assert len(df) == 2
        assert df.iloc[0]["repair_id"] == "X001"

    def test_missing_column_raises(self):
        df = pd.DataFrame([{"foo": "bar"}])
        with pytest.raises(ValueError, match="必須カラムが不足"):
            split_records(df)


# =============================================================================
# Internal の複製設定
# =============================================================================

class TestInternalDuplication:
    def test_default_duplicates(self, sample_records_well_formed_split):
        df, _ = split_records(sample_records_well_formed_split)
        r101 = df[df["repair_id"] == "R101"]
        assert (r101["internal_1"] == "顧客優先").all()

    def test_no_duplicate(self, sample_records_well_formed_split):
        config = SplitConfig(duplicate_internal_to_all=False)
        df, _ = split_records(sample_records_well_formed_split, config)
        r101 = df[df["repair_id"] == "R101"].sort_values("sub_id")
        assert r101.iloc[0]["internal_1"] == "顧客優先"
        assert r101.iloc[1]["internal_1"] == ""


# =============================================================================
# レポート構造
# =============================================================================

class TestReport:
    def test_summary_format(self, sample_records_well_formed_split):
        _, report = split_records(sample_records_well_formed_split)
        s = report.summary()
        assert "分割処理レポート" in s
        assert "重複マーカー" in s

    def test_empty_input(self):
        df = pd.DataFrame(columns=[
            "repair_id", "product_type", "user_comment",
            "repair_comment", "internal_1", "internal_2",
        ])
        out_df, report = split_records(df)
        assert len(out_df) == 0
        assert report.total_input_records == 0


# =============================================================================
# NaN処理
# =============================================================================

class TestNaNHandling:
    def test_nan_columns(self):
        df = pd.DataFrame([{
            "repair_id": "N001",
            "product_type": "ML",
            "user_comment": float("nan"),
            "repair_comment": None,
            "internal_1": float("nan"),
            "internal_2": None,
        }])
        out_df, report = split_records(df)
        assert len(out_df) == 1
        assert out_df.iloc[0]["user_text"] == ""
        assert report.empty_record_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
