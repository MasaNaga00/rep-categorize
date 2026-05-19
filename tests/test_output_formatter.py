"""
test_output_formatter.py
========================
output_formatter.py の単体テスト。

実 yaml + 合成データで、ワイド/ロング/集約形式の挙動を検証する。
write_all_formats は tmp_path に出力して確認する。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from derive_metrics import build_derived_dataframe
from output_formatter import (
    _all_true,
    _any_false,
    _any_true,
    _first_non_null,
    _join_codes,
    _min_value,
    to_aggregated_format,
    to_long_format,
    to_wide_format,
    write_all_formats,
    write_csv,
)


# =============================================================================
# 共通fixture / ヘルパー
# =============================================================================

def _make_record(repair_id: str, sub_id: int = 1, product_type: str = "ML", **kwargs) -> dict:
    base = {
        "repair_id": repair_id,
        "sub_id": sub_id,
        "product_type": product_type,
        "user_text": "AFが効きません",
        "user_context": "",
        "repair_text": "AFユニット交換",
        "repair_context": "",
        "internal_1": "",
        "internal_2": "",
    }
    base.update(kwargs)
    return base


def _make_classification(
    repair_id: str,
    sub_id: int = 1,
    user_code: str = "M005",
    repair_code: str = "M005",
    user_conf: float = 0.9,
    repair_conf: float = 0.95,
    reproduction_status: str = "reproduced",
    reproduction_conf: float = 0.8,
    env_factors: list[str] | None = None,
    env_source: dict | None = None,
    env_conf: dict | None = None,
) -> dict:
    if env_factors is None:
        env_factors = ["unknown"]
    if env_source is None:
        env_source = {"unknown": "user"}
    if env_conf is None:
        env_conf = {"unknown": 1.0}
    return {
        "repair_id": repair_id,
        "sub_id": sub_id,
        "user_perspective": {
            "failure_category_code": user_code,
            "confidence": user_conf,
            "evidence": "test",
            "insufficient_info": False,
        },
        "repair_perspective": {
            "failure_category_code": repair_code,
            "confidence": repair_conf,
            "evidence": "test",
            "insufficient_info": False,
        },
        "reproduction_status": reproduction_status,
        "reproduction_evidence": "test",
        "reproduction_confidence": reproduction_conf,
        "environment_factors": env_factors,
        "environment_evidence_source": env_source,
        "environment_evidence": {k: f"ev-{k}" for k in env_factors},
        "environment_confidence": env_conf,
    }


@pytest.fixture
def derived_df(codebook):
    """build_derived_dataframe の出力相当 (3件、多様なパターン)。"""
    records = [
        _make_record("R001", sub_id=1, product_type="ML"),
        _make_record("R001", sub_id=2, product_type="ML"),
        _make_record("R002", sub_id=1, product_type="LENS"),
    ]
    classifications = [
        # ケース1: 視点不一致 + 環境水 + 修理者確認
        _make_classification(
            "R001", sub_id=1, user_code="M012", repair_code="M013",
            env_factors=["water"], env_source={"water": "repair"},
            env_conf={"water": 0.9},
        ),
        # ケース2: サービスレコード
        _make_classification(
            "R001", sub_id=2, user_code="M042", repair_code="M042",
            env_factors=["none"], env_source={"none": "user"},
            env_conf={"none": 1.0},
        ),
        # ケース3: LENS 環境要因なし
        _make_classification(
            "R002", sub_id=1, user_code="L001", repair_code="L001",
            env_factors=["unknown"], env_source={"unknown": "user"},
            env_conf={"unknown": 1.0},
        ),
    ]
    return build_derived_dataframe(records, classifications, codebook)


# =============================================================================
# ワイド形式
# =============================================================================

class TestToWideFormat:

    def test_returns_dataframe(self, derived_df):
        wide = to_wide_format(derived_df)
        assert isinstance(wide, pd.DataFrame)
        assert len(wide) == 3

    def test_contains_essential_columns(self, derived_df):
        wide = to_wide_format(derived_df)
        essential = {
            "repair_id", "sub_id", "product_type",
            "user_failure_code", "user_failure_name",
            "repair_failure_code", "repair_failure_name",
            "reproduction_status", "environment_factors_str",
            "has_water", "has_impact",
            "perspective_match", "is_misjudged",
            "is_service_record", "min_confidence",
        }
        assert essential.issubset(set(wide.columns))

    def test_no_dict_columns(self, derived_df):
        """dict 型カラムはワイド形式に含めない (環境系の dict)。"""
        wide = to_wide_format(derived_df)
        assert "environment_evidence_source" not in wide.columns
        assert "environment_evidence" not in wide.columns
        assert "environment_confidence" not in wide.columns
        assert "environment_factors" not in wide.columns  # list も含めない

    def test_column_order_stable(self, derived_df):
        """カラム順序が想定の通り (repair_id が先頭)。"""
        wide = to_wide_format(derived_df)
        assert list(wide.columns)[0] == "repair_id"
        assert list(wide.columns)[1] == "sub_id"

    def test_preserves_values(self, derived_df):
        wide = to_wide_format(derived_df)
        r1 = wide[(wide["repair_id"] == "R001") & (wide["sub_id"] == 1)].iloc[0]
        assert r1["user_failure_code"] == "M012"
        assert r1["repair_failure_code"] == "M013"
        assert r1["has_water"]
        assert r1["is_misjudged"]


# =============================================================================
# ロング形式
# =============================================================================

class TestToLongFormat:

    def test_explodes_environment_factors(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification(
            "R001",
            env_factors=["water", "impact"],
            env_source={"water": "user", "impact": "repair"},
            env_conf={"water": 0.9, "impact": 0.8},
        )]
        df = build_derived_dataframe(records, classifications, codebook)
        long_df = to_long_format(df)
        assert len(long_df) == 2
        assert set(long_df["environment_factor"]) == {"water", "impact"}

    def test_picks_correct_source_per_factor(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification(
            "R001",
            env_factors=["water", "impact"],
            env_source={"water": "user", "impact": "repair"},
            env_conf={"water": 0.9, "impact": 0.8},
        )]
        df = build_derived_dataframe(records, classifications, codebook)
        long_df = to_long_format(df)
        water_row = long_df[long_df["environment_factor"] == "water"].iloc[0]
        impact_row = long_df[long_df["environment_factor"] == "impact"].iloc[0]
        assert water_row["environment_evidence_source"] == "user"
        assert impact_row["environment_evidence_source"] == "repair"
        assert water_row["environment_confidence"] == 0.9
        assert impact_row["environment_confidence"] == 0.8

    def test_single_factor_yields_one_row(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", env_factors=["water"],
                                                env_source={"water": "user"},
                                                env_conf={"water": 0.9})]
        df = build_derived_dataframe(records, classifications, codebook)
        long_df = to_long_format(df)
        assert len(long_df) == 1

    def test_carries_failure_codes(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M007", repair_code="M008",
                                                env_factors=["water"],
                                                env_source={"water": "user"},
                                                env_conf={"water": 0.9})]
        df = build_derived_dataframe(records, classifications, codebook)
        long_df = to_long_format(df)
        assert long_df.iloc[0]["user_failure_code"] == "M007"
        assert long_df.iloc[0]["repair_failure_code"] == "M008"

    def test_multi_records_multi_factors(self, derived_df):
        """3件のうち各レコードで1つずつ環境要因 → 3行になる。"""
        long_df = to_long_format(derived_df)
        # R001-1: water, R001-2: none, R002-1: unknown → 3行
        assert len(long_df) == 3

    def test_empty_env_factors_dropped(self, codebook):
        """environment_factors=[] のレコードはロング形式に出てこない。"""
        records = [_make_record("R001"), _make_record("R002")]
        classifications = [
            _make_classification("R001", env_factors=["water"],
                                 env_source={"water": "user"}, env_conf={"water": 0.9}),
            _make_classification("R002", env_factors=[], env_source={}, env_conf={}),
        ]
        df = build_derived_dataframe(records, classifications, codebook)
        long_df = to_long_format(df)
        # R002 は env_factors=[] で消える
        assert len(long_df) == 1
        assert long_df.iloc[0]["repair_id"] == "R001"


# =============================================================================
# 集約形式
# =============================================================================

class TestToAggregatedFormat:

    def test_one_row_per_repair_id(self, derived_df):
        agg = to_aggregated_format(derived_df)
        assert len(agg) == 2  # R001, R002
        assert set(agg["repair_id"]) == {"R001", "R002"}

    def test_total_sub_records(self, derived_df):
        agg = to_aggregated_format(derived_df)
        r001 = agg[agg["repair_id"] == "R001"].iloc[0]
        r002 = agg[agg["repair_id"] == "R002"].iloc[0]
        assert r001["total_sub_records"] == 2
        assert r002["total_sub_records"] == 1

    def test_codes_joined_comma(self, derived_df):
        agg = to_aggregated_format(derived_df)
        r001 = agg[agg["repair_id"] == "R001"].iloc[0]
        # M012, M042 のはず
        assert "M012" in r001["user_failure_codes"]
        assert "M042" in r001["user_failure_codes"]

    def test_any_misjudged(self, derived_df):
        """R001 は misjudged を1件含む → any_misjudged=True、R002 は False。"""
        agg = to_aggregated_format(derived_df)
        r001 = agg[agg["repair_id"] == "R001"].iloc[0]
        r002 = agg[agg["repair_id"] == "R002"].iloc[0]
        assert r001["any_misjudged"]
        assert not r002["any_misjudged"]

    def test_any_harsh_env(self, derived_df):
        agg = to_aggregated_format(derived_df)
        r001 = agg[agg["repair_id"] == "R001"].iloc[0]
        r002 = agg[agg["repair_id"] == "R002"].iloc[0]
        # R001 sub_id=1 が water → True、R002 は unknown のみ → False
        assert r001["any_harsh_env"]
        assert not r002["any_harsh_env"]

    def test_all_service_records(self, codebook):
        """全 sub_id がサービスの場合 True。"""
        records = [_make_record("R100", sub_id=1), _make_record("R100", sub_id=2)]
        classifications = [
            _make_classification("R100", sub_id=1, repair_code="M042"),
            _make_classification("R100", sub_id=2, repair_code="M043"),
        ]
        df = build_derived_dataframe(records, classifications, codebook)
        agg = to_aggregated_format(df)
        assert agg.iloc[0]["all_service_records"]

    def test_not_all_service_records_mixed(self, codebook):
        records = [_make_record("R100", sub_id=1), _make_record("R100", sub_id=2)]
        classifications = [
            _make_classification("R100", sub_id=1, repair_code="M042"),
            _make_classification("R100", sub_id=2, repair_code="M005"),
        ]
        df = build_derived_dataframe(records, classifications, codebook)
        agg = to_aggregated_format(df)
        assert not agg.iloc[0]["all_service_records"]

    def test_min_confidence_overall(self, derived_df):
        agg = to_aggregated_format(derived_df)
        for _, row in agg.iterrows():
            # min_confidence_overall は input の min_confidence の最小値
            assert isinstance(row["min_confidence_overall"], float)


# =============================================================================
# helper unit tests
# =============================================================================

class TestHelpers:

    def test_first_non_null(self):
        assert _first_non_null(pd.Series(["A", "B"])) == "A"
        assert _first_non_null(pd.Series([None, "B"])) == "B"
        assert _first_non_null(pd.Series([None, None])) is None
        assert _first_non_null(None) is None

    def test_join_codes(self):
        assert _join_codes(pd.Series(["M005", "M007", "M005"])) == "M005,M007"
        assert _join_codes(pd.Series([])) == ""
        assert _join_codes(pd.Series([None, "M005"])) == "M005"

    def test_any_true(self):
        assert _any_true(pd.Series([True, False]))
        assert not _any_true(pd.Series([False, False]))
        assert not _any_true(pd.Series([None, False]))
        assert not _any_true(pd.Series([]))

    def test_any_false(self):
        assert _any_false(pd.Series([True, False]))
        assert not _any_false(pd.Series([True, True]))
        assert not _any_false(pd.Series([]))

    def test_all_true(self):
        assert _all_true(pd.Series([True, True]))
        assert not _all_true(pd.Series([True, False]))
        assert not _all_true(pd.Series([None, None]))
        assert not _all_true(pd.Series([]))

    def test_min_value(self):
        assert _min_value(pd.Series([0.5, 0.7])) == 0.5
        assert _min_value(pd.Series([0.5, None])) == 0.5
        result = _min_value(pd.Series([None, None]))
        assert pd.isna(result)
        result = _min_value(pd.Series([]))
        assert pd.isna(result)


# =============================================================================
# CSV書き出し
# =============================================================================

class TestWriteCsv:

    def test_writes_utf8_bom(self, tmp_path):
        df = pd.DataFrame({"a": ["あ", "い"], "b": [1, 2]})
        path = tmp_path / "test.csv"
        write_csv(df, path)
        # 先頭は UTF-8 BOM (EF BB BF)
        raw = path.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"

    def test_stringifies_dict_columns(self, tmp_path):
        df = pd.DataFrame({
            "id": ["R001"],
            "data": [{"key": "val"}],
        })
        path = tmp_path / "test.csv"
        write_csv(df, path)
        text = path.read_text(encoding="utf-8-sig")
        assert '"{""key"": ""val""}"' in text or '{"key": "val"}' in text

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "deep" / "dir" / "out.csv"
        write_csv(pd.DataFrame({"a": [1]}), path)
        assert path.exists()


# =============================================================================
# パイプライン (write_all_formats)
# =============================================================================

class TestWriteAllFormats:

    def test_creates_all_files(self, derived_df, tmp_path, codebook):
        paths = write_all_formats(
            derived_df, output_dir=tmp_path,
            timestamp="20260513_120000", codebook=codebook,
        )
        assert paths["wide"].exists()
        assert paths["long"].exists()
        assert paths["aggregated"].exists()
        assert paths["meta"].exists()

    def test_filenames_use_timestamp(self, derived_df, tmp_path, codebook):
        paths = write_all_formats(
            derived_df, output_dir=tmp_path,
            timestamp="20260513_120000", codebook=codebook,
        )
        assert paths["wide"].name == "20260513_120000_wide.csv"
        assert paths["long"].name == "20260513_120000_long.csv"
        assert paths["aggregated"].name == "20260513_120000_aggregated.csv"
        assert paths["meta"].name == "20260513_120000_output_meta.json"

    def test_wide_csv_readable_with_japanese(self, derived_df, tmp_path, codebook):
        paths = write_all_formats(
            derived_df, output_dir=tmp_path,
            timestamp="ts1", codebook=codebook,
        )
        # 日本語が壊れずに読み戻せる
        wide_df = pd.read_csv(paths["wide"], encoding="utf-8-sig")
        # user_text に日本語
        assert any("AF" in s for s in wide_df["user_text"].astype(str))

    def test_long_csv_has_environment_factor(self, derived_df, tmp_path, codebook):
        paths = write_all_formats(
            derived_df, output_dir=tmp_path,
            timestamp="ts2", codebook=codebook,
        )
        long_df = pd.read_csv(paths["long"], encoding="utf-8-sig")
        assert "environment_factor" in long_df.columns

    def test_aggregated_csv_one_row_per_repair_id(self, derived_df, tmp_path, codebook):
        paths = write_all_formats(
            derived_df, output_dir=tmp_path,
            timestamp="ts3", codebook=codebook,
        )
        agg_df = pd.read_csv(paths["aggregated"], encoding="utf-8-sig")
        assert len(agg_df) == derived_df["repair_id"].nunique()

    def test_meta_json_has_counts(self, derived_df, tmp_path, codebook):
        paths = write_all_formats(
            derived_df, output_dir=tmp_path,
            timestamp="ts4", codebook=codebook,
        )
        meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
        assert meta["timestamp"] == "ts4"
        assert "counts" in meta
        assert meta["counts"]["input_rows"] == len(derived_df)
        assert meta["counts"]["unique_repair_ids"] == derived_df["repair_id"].nunique()
        assert "derived_metrics_summary" in meta

    def test_meta_includes_product_type_counts(self, derived_df, tmp_path, codebook):
        paths = write_all_formats(
            derived_df, output_dir=tmp_path,
            timestamp="ts5", codebook=codebook,
        )
        meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
        assert "product_type_counts" in meta
        # derived_df は ML が 2件、LENS が 1件
        assert meta["product_type_counts"].get("ML") == 2
        assert meta["product_type_counts"].get("LENS") == 1
