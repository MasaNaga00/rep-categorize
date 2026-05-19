"""
test_derive_metrics.py
======================
derive_metrics.py の単体テスト。

実 yaml (config/classification_codes.yaml) を使った CodeBook と、
合成データで検証する。

テストグループ:
    - TestMerge: records と classifications の結合
    - TestPerspectiveMatch: perspective_match
    - TestResponsibility: manufacturer 責任判定 / is_misjudged
    - TestEnvMetrics: has_harsh_env, has_repair_confirmed_env
    - TestServiceRecord: is_service_record
    - TestMinConfidence: min_confidence
    - TestExpandEnvFlags: has_water 等のフラグ展開
    - TestFailureNames: コード名付与
    - TestBuildDerivedDataframe: パイプライン全体
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from derive_metrics import (
    _compute_min_confidence,
    _has_harsh_env,
    _has_repair_confirmed,
    add_failure_names,
    build_derived_dataframe,
    calculate_derived_metrics,
    expand_environment_flags,
    merge_records_and_classifications,
)


# =============================================================================
# 共通fixture / ヘルパー
# =============================================================================

def _make_record(repair_id: str, sub_id: int = 1, product_type: str = "ML", **kwargs) -> dict:
    """records.json 形式のサンプル1件。"""
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
    """classifications.json 形式のサンプル1件。"""
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
            "evidence": "test evidence",
            "insufficient_info": False,
        },
        "repair_perspective": {
            "failure_category_code": repair_code,
            "confidence": repair_conf,
            "evidence": "test evidence",
            "insufficient_info": False,
        },
        "reproduction_status": reproduction_status,
        "reproduction_evidence": "test",
        "reproduction_confidence": reproduction_conf,
        "environment_factors": env_factors,
        "environment_evidence_source": env_source,
        "environment_evidence": {k: "test" for k in env_factors},
        "environment_confidence": env_conf,
    }


@pytest.fixture
def sample_pair():
    """1件ペアの (records, classifications)。"""
    records = [_make_record("R001", 1)]
    classifications = [_make_classification("R001", 1)]
    return records, classifications


@pytest.fixture
def merged_df(sample_pair):
    """結合済み DataFrame (派生指標計算前)。"""
    records, classifications = sample_pair
    return merge_records_and_classifications(records, classifications)


# =============================================================================
# 結合
# =============================================================================

class TestMerge:

    def test_basic_merge(self, sample_pair):
        records, classifications = sample_pair
        df = merge_records_and_classifications(records, classifications)
        assert len(df) == 1
        assert df.iloc[0]["repair_id"] == "R001"
        assert df.iloc[0]["sub_id"] == 1
        assert df.iloc[0]["user_text"] == "AFが効きません"
        assert df.iloc[0]["user_failure_code"] == "M005"
        assert df.iloc[0]["repair_failure_code"] == "M005"

    def test_renames_perspective_columns(self, merged_df):
        """ネストされた perspective_xxx カラムが短縮名にリネームされている。"""
        cols = set(merged_df.columns)
        assert "user_failure_code" in cols
        assert "user_confidence" in cols
        assert "user_evidence" in cols
        assert "repair_failure_code" in cols
        assert "repair_confidence" in cols
        assert "user_perspective_failure_category_code" not in cols
        assert "repair_perspective_failure_category_code" not in cols

    def test_preserves_list_columns(self, merged_df):
        """environment_factors は list のまま保持される。"""
        row = merged_df.iloc[0]
        assert isinstance(row["environment_factors"], list)

    def test_multi_records(self):
        records = [_make_record("R001"), _make_record("R002"), _make_record("R003")]
        classifications = [
            _make_classification("R001"),
            _make_classification("R002"),
            _make_classification("R003"),
        ]
        df = merge_records_and_classifications(records, classifications)
        assert len(df) == 3
        assert set(df["repair_id"]) == {"R001", "R002", "R003"}

    def test_subid_correctly_matched(self):
        """sub_id が異なる場合、正しく対応付けされる。"""
        records = [
            _make_record("R001", sub_id=1, user_text="現象1"),
            _make_record("R001", sub_id=2, user_text="現象2"),
        ]
        classifications = [
            _make_classification("R001", sub_id=1, user_code="M001"),
            _make_classification("R001", sub_id=2, user_code="M002"),
        ]
        df = merge_records_and_classifications(records, classifications)
        assert len(df) == 2
        r1 = df[df["sub_id"] == 1].iloc[0]
        r2 = df[df["sub_id"] == 2].iloc[0]
        assert r1["user_text"] == "現象1"
        assert r1["user_failure_code"] == "M001"
        assert r2["user_text"] == "現象2"
        assert r2["user_failure_code"] == "M002"

    def test_empty_records_raises(self):
        with pytest.raises(ValueError, match="records"):
            merge_records_and_classifications([], [_make_classification("R001")])

    def test_empty_classifications_raises(self):
        with pytest.raises(ValueError, match="classifications"):
            merge_records_and_classifications([_make_record("R001")], [])

    def test_no_overlap_raises(self):
        """主キーが全く重ならない場合、inner join 結果が 0 件で raise。"""
        records = [_make_record("R001")]
        classifications = [_make_classification("R999")]
        with pytest.raises(ValueError, match="0 件"):
            merge_records_and_classifications(records, classifications)

    def test_count_mismatch_warns(self, caplog):
        """件数差があると warning を出す。"""
        records = [_make_record("R001"), _make_record("R002")]
        classifications = [_make_classification("R001")]
        with caplog.at_level(logging.WARNING):
            df = merge_records_and_classifications(records, classifications)
        assert len(df) == 1
        assert any("件数が異なります" in r.message for r in caplog.records)

    def test_left_join_preserves_records(self):
        """how='left' を指定すると失敗バッチも残る。"""
        records = [_make_record("R001"), _make_record("R002")]
        classifications = [_make_classification("R001")]
        df = merge_records_and_classifications(records, classifications, how="left")
        assert len(df) == 2
        r2 = df[df["repair_id"] == "R002"].iloc[0]
        assert pd.isna(r2["user_failure_code"])

    def test_missing_key_column_raises(self):
        records = [{"sub_id": 1, "user_text": "x"}]  # repair_id なし
        with pytest.raises(ValueError, match="repair_id"):
            merge_records_and_classifications(records, [_make_classification("R001")])


# =============================================================================
# perspective_match
# =============================================================================

class TestPerspectiveMatch:

    def test_matching_codes(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M005", repair_code="M005")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert bool(df.iloc[0]["perspective_match"])

    def test_mismatching_codes(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M005", repair_code="M007")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert not df.iloc[0]["perspective_match"]


# =============================================================================
# responsibility (is_manufacturer_responsibility_xxx, is_misjudged)
# =============================================================================

class TestResponsibility:
    """
    responsibility は yaml v0.2.0 で M012/M013/M014/M015 のみ付与済み。
    その他のコードは未付与のため、is_manufacturer_responsibility=False となる前提
    (00_common.md の決定事項 α 通り。yaml 未更新)。
    """

    def test_m012_is_manufacturer(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M012", repair_code="M012")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert df.iloc[0]["is_manufacturer_responsibility_user"]
        assert df.iloc[0]["is_manufacturer_responsibility_repair"]
        assert not df.iloc[0]["is_misjudged"]  # 両方 True なので食い違いなし

    def test_m013_is_not_manufacturer(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M013", repair_code="M013")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert not df.iloc[0]["is_manufacturer_responsibility_user"]
        assert not df.iloc[0]["is_manufacturer_responsibility_repair"]

    def test_misjudged_m012_vs_m013(self, codebook):
        """センサー内 (manufacturer) vs センサー外 (user) で is_misjudged=True。"""
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M012", repair_code="M013")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert df.iloc[0]["is_misjudged"]

    def test_unset_responsibility_treated_as_false(self, codebook):
        """responsibility 未付与のコードは False 扱い (α 仕様)。"""
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M001", repair_code="M001")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert not df.iloc[0]["is_manufacturer_responsibility_user"]
        assert not df.iloc[0]["is_manufacturer_responsibility_repair"]
        assert not df.iloc[0]["is_misjudged"]  # 両方 False なので食い違いなし

    def test_unknown_code_safe(self, codebook):
        """体系外コードでも例外を出さず False。"""
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M999", repair_code="M999")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert not df.iloc[0]["is_manufacturer_responsibility_user"]

    def test_lens_product_type(self, codebook):
        """LENS の場合も同様に動作する。"""
        records = [_make_record("R001", product_type="LENS")]
        classifications = [_make_classification("R001", user_code="L001", repair_code="L001")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        # L001 は responsibility 未付与なので False
        assert not df.iloc[0]["is_manufacturer_responsibility_user"]


# =============================================================================
# 環境要因 (has_harsh_env, has_repair_confirmed_env)
# =============================================================================

class TestEnvMetrics:

    def test_has_harsh_env_when_water(self):
        assert _has_harsh_env(["water"])

    def test_has_harsh_env_when_multiple_with_harsh(self):
        assert _has_harsh_env(["water", "none"])

    def test_no_harsh_env_when_only_none(self):
        assert not _has_harsh_env(["none"])

    def test_no_harsh_env_when_only_unknown(self):
        assert not _has_harsh_env(["unknown"])

    def test_no_harsh_env_when_empty(self):
        assert not _has_harsh_env([])

    def test_no_harsh_env_when_not_list(self):
        assert not _has_harsh_env(None)
        assert not _has_harsh_env("water")

    def test_has_repair_confirmed_env_when_repair(self):
        assert _has_repair_confirmed({"water": "repair"})

    def test_has_repair_confirmed_env_when_both(self):
        assert _has_repair_confirmed({"water": "both"})

    def test_no_repair_confirmed_when_only_user(self):
        assert not _has_repair_confirmed({"water": "user"})

    def test_no_repair_confirmed_when_empty(self):
        assert not _has_repair_confirmed({})

    def test_no_repair_confirmed_when_not_dict(self):
        assert not _has_repair_confirmed(None)
        assert not _has_repair_confirmed([])

    def test_integrated_harsh_and_repair_confirmed(self, codebook):
        """有害環境かつ修理者確認ありのケース。"""
        records = [_make_record("R001")]
        classifications = [_make_classification(
            "R001",
            env_factors=["water"],
            env_source={"water": "repair"},
            env_conf={"water": 0.95},
        )]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert df.iloc[0]["has_harsh_env"]
        assert df.iloc[0]["has_repair_confirmed_env"]


# =============================================================================
# サービスレコード
# =============================================================================

class TestServiceRecord:

    def test_m042_is_service(self, codebook):
        """M042 (検査) はサービスレコード。"""
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", repair_code="M042")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert df.iloc[0]["is_service_record"]

    def test_m005_is_not_service(self, codebook):
        """通常故障コードはサービスレコードではない。"""
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", repair_code="M005")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert not df.iloc[0]["is_service_record"]

    def test_lens_service_code(self, codebook):
        """LENS のサービスコードも判定可能。"""
        records = [_make_record("R001", product_type="LENS")]
        classifications = [_make_classification("R001", repair_code="L032")]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        assert df.iloc[0]["is_service_record"]


# =============================================================================
# min_confidence
# =============================================================================

class TestMinConfidence:

    def test_returns_minimum_of_all_tasks(self):
        row = pd.Series({
            "user_confidence": 0.9,
            "repair_confidence": 0.95,
            "reproduction_confidence": 0.7,
            "environment_confidence": {"water": 0.85},
        })
        assert _compute_min_confidence(row) == 0.7

    def test_env_confidence_can_be_minimum(self):
        row = pd.Series({
            "user_confidence": 0.9,
            "repair_confidence": 0.95,
            "reproduction_confidence": 0.85,
            "environment_confidence": {"water": 0.5},
        })
        assert _compute_min_confidence(row) == 0.5

    def test_multiple_env_factors_uses_min(self):
        row = pd.Series({
            "user_confidence": 0.9,
            "repair_confidence": 0.95,
            "reproduction_confidence": 0.85,
            "environment_confidence": {"water": 0.6, "impact": 0.3},
        })
        assert _compute_min_confidence(row) == 0.3

    def test_handles_missing_env(self):
        row = pd.Series({
            "user_confidence": 0.9,
            "repair_confidence": 0.8,
            "reproduction_confidence": 0.7,
            "environment_confidence": None,
        })
        assert _compute_min_confidence(row) == 0.7

    def test_handles_all_missing(self):
        row = pd.Series({
            "user_confidence": None,
            "repair_confidence": None,
            "reproduction_confidence": None,
            "environment_confidence": None,
        })
        result = _compute_min_confidence(row)
        assert pd.isna(result)


# =============================================================================
# expand_environment_flags
# =============================================================================

class TestExpandEnvFlags:

    def test_water_flag_set(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", env_factors=["water"])]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        df = expand_environment_flags(df)
        assert df.iloc[0]["has_water"]
        assert not df.iloc[0]["has_impact"]
        assert not df.iloc[0]["has_heat"]

    def test_multiple_flags(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification(
            "R001",
            env_factors=["water", "impact"],
            env_source={"water": "user", "impact": "repair"},
            env_conf={"water": 0.9, "impact": 0.8},
        )]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        df = expand_environment_flags(df)
        row = df.iloc[0]
        assert row["has_water"]
        assert row["has_impact"]
        assert not row["has_heat"]
        assert not row["has_sand_dust"]

    def test_none_does_not_set_flag(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", env_factors=["none"])]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        df = expand_environment_flags(df)
        row = df.iloc[0]
        for key in ("water", "sand_dust", "impact", "heat", "cold", "humidity"):
            assert not row[f"has_{key}"]

    def test_environment_factors_str(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification(
            "R001",
            env_factors=["water", "impact"],
            env_source={"water": "user", "impact": "user"},
            env_conf={"water": 0.9, "impact": 0.8},
        )]
        df = merge_records_and_classifications(records, classifications)
        df = calculate_derived_metrics(df, codebook)
        df = expand_environment_flags(df)
        assert df.iloc[0]["environment_factors_str"] == "water,impact"


# =============================================================================
# add_failure_names
# =============================================================================

class TestFailureNames:

    def test_known_code_returns_name(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M005", repair_code="M005")]
        df = merge_records_and_classifications(records, classifications)
        df = add_failure_names(df, codebook)
        # M005 = AF不良
        assert df.iloc[0]["user_failure_name"] == "AF不良"
        assert df.iloc[0]["repair_failure_name"] == "AF不良"

    def test_different_codes(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M005", repair_code="M042")]
        df = merge_records_and_classifications(records, classifications)
        df = add_failure_names(df, codebook)
        assert df.iloc[0]["user_failure_name"] == "AF不良"
        assert df.iloc[0]["repair_failure_name"]
        assert df.iloc[0]["repair_failure_name"] != df.iloc[0]["user_failure_name"]

    def test_unknown_code_returns_empty(self, codebook):
        records = [_make_record("R001")]
        classifications = [_make_classification("R001", user_code="M999", repair_code="M005")]
        df = merge_records_and_classifications(records, classifications)
        df = add_failure_names(df, codebook)
        assert df.iloc[0]["user_failure_name"] == ""
        assert df.iloc[0]["repair_failure_name"]  # M005 はある

    def test_lens_code(self, codebook):
        records = [_make_record("R001", product_type="LENS")]
        classifications = [_make_classification("R001", user_code="L001", repair_code="L001")]
        df = merge_records_and_classifications(records, classifications)
        df = add_failure_names(df, codebook)
        assert df.iloc[0]["user_failure_name"]  # L001 の name が取得できる


# =============================================================================
# パイプライン全体
# =============================================================================

class TestBuildDerivedDataframe:

    def test_end_to_end(self, codebook):
        records = [
            _make_record("R001", sub_id=1, product_type="ML"),
            _make_record("R001", sub_id=2, product_type="ML"),
            _make_record("R002", sub_id=1, product_type="LENS"),
        ]
        classifications = [
            _make_classification("R001", sub_id=1, user_code="M012", repair_code="M013",
                                 env_factors=["water"], env_source={"water": "repair"},
                                 env_conf={"water": 0.9}),
            _make_classification("R001", sub_id=2, user_code="M042", repair_code="M042"),
            _make_classification("R002", sub_id=1, user_code="L001", repair_code="L001"),
        ]
        df = build_derived_dataframe(records, classifications, codebook)
        assert len(df) == 3

        expected_cols = {
            "repair_id", "sub_id", "product_type", "user_text", "repair_text",
            "user_failure_code", "user_failure_name", "user_confidence",
            "repair_failure_code", "repair_failure_name", "repair_confidence",
            "reproduction_status", "reproduction_confidence",
            "environment_factors", "environment_factors_str",
            "has_water", "has_impact", "has_heat",
            "perspective_match", "is_misjudged",
            "is_manufacturer_responsibility_user",
            "is_manufacturer_responsibility_repair",
            "has_harsh_env", "has_repair_confirmed_env",
            "is_service_record", "min_confidence",
        }
        assert expected_cols.issubset(set(df.columns))

        # R001 sub_id=1: M012(mfr) vs M013(user_or_unknown), water/repair
        r1 = df[(df["repair_id"] == "R001") & (df["sub_id"] == 1)].iloc[0]
        assert r1["is_misjudged"]
        assert r1["has_water"]
        assert r1["has_harsh_env"]
        assert r1["has_repair_confirmed_env"]
        assert not r1["perspective_match"]

        # R001 sub_id=2: M042 = service record
        r2 = df[(df["repair_id"] == "R001") & (df["sub_id"] == 2)].iloc[0]
        assert r2["is_service_record"]
        assert r2["perspective_match"]
