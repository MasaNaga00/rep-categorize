"""
test_codes_loader.py
====================
codes_loader.py の単体テスト。

正常系: 実YAMLがロードできる、ヘルパー関数が正しく動く
異常系: 不正YAMLで適切にエラーになる
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

# srcをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from codes_loader import (
    CodeBook,
    CodeBookLoadError,
    DescriptionStatus,
    EnvSource,
    ProductType,
    RecordType,
    Responsibility,
    load_codes,
)


# =============================================================================
# fixtures
# =============================================================================

REAL_YAML_PATH = Path(__file__).parent.parent / "config" / "classification_codes.yaml"


def _minimal_valid_yaml_dict() -> dict:
    """バリデーションが通る最小のYAML辞書を返す。"""
    return {
        "meta": {"version": "0.0.1"},
        "product_categories": {
            "ML": {
                "name": "ミラーレスカメラ",
                "failure_categories": {
                    "M001": {
                        "name": "テスト故障",
                        "description": "テスト用",
                        "record_type": "failure",
                    },
                    "M_OTHER": {
                        "name": "その他",
                        "description": "その他テスト",
                        "record_type": "failure",
                        "is_special": True,
                    },
                    "M_UNK": {
                        "name": "判定不能",
                        "description": "判定不能テスト",
                        "record_type": "unknown",
                        "is_special": True,
                    },
                },
            },
            "LENS": {
                "name": "交換レンズ",
                "failure_categories": {
                    "L001": {
                        "name": "テスト故障L",
                        "description": "テスト用L",
                        "record_type": "failure",
                    },
                    "L_OTHER": {
                        "name": "その他",
                        "description": "その他テスト",
                        "record_type": "failure",
                        "is_special": True,
                    },
                    "L_UNK": {
                        "name": "判定不能",
                        "description": "判定不能テスト",
                        "record_type": "unknown",
                        "is_special": True,
                    },
                },
            },
        },
        "environment_factors": {
            "water": {"name": "水濡れ", "description": "テスト"},
            "none": {"name": "該当なし", "description": "テスト", "is_special": True},
            "unknown": {"name": "不明", "description": "テスト", "is_special": True},
        },
        "reproduction_statuses": {
            "reproduced": {"name": "再現あり", "description": "テスト"},
            "not_reproduced": {"name": "再現せず", "description": "テスト"},
            "partial": {"name": "条件付き", "description": "テスト"},
            "not_attempted": {"name": "確認なし", "description": "テスト"},
        },
    }


@pytest.fixture
def tmp_yaml(tmp_path: Path):
    """テスト用YAMLファイルを生成するファクトリ。"""
    def _create(data: dict) -> Path:
        p = tmp_path / "test.yaml"
        p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return p
    return _create


# =============================================================================
# 正常系テスト
# =============================================================================

class TestLoadValid:
    """正常系: 有効なYAMLがロードできる。"""

    def test_load_real_yaml(self):
        """実プロジェクトのYAMLがロードできる。"""
        codebook = load_codes(REAL_YAML_PATH)
        assert isinstance(codebook, CodeBook)
        assert codebook.meta.version == "0.2.0"
        # ML 47項目（M001-M046 + M_UNK）、LENS 37項目（L001-L036 + L_UNK）
        assert len(codebook.product_categories["ML"].failure_categories) == 47
        assert len(codebook.product_categories["LENS"].failure_categories) == 37

    def test_load_minimal_valid(self, tmp_yaml):
        """最小有効YAMLがロードできる。"""
        path = tmp_yaml(_minimal_valid_yaml_dict())
        codebook = load_codes(path)
        assert codebook.meta.version == "0.0.1"


# =============================================================================
# 異常系テスト: ファイル・YAML構文
# =============================================================================

class TestLoadFileErrors:
    """ファイル・YAML構文系の異常系。"""

    def test_file_not_found(self):
        """存在しないパスは CodeBookLoadError。"""
        with pytest.raises(CodeBookLoadError, match="見つかりません"):
            load_codes("/nonexistent/path.yaml")

    def test_invalid_yaml_syntax(self, tmp_path):
        """壊れたYAMLは CodeBookLoadError。"""
        p = tmp_path / "broken.yaml"
        p.write_text("foo: [bar: baz\n", encoding="utf-8")  # 構文壊し
        with pytest.raises(CodeBookLoadError, match="YAML構文エラー"):
            load_codes(p)

    def test_yaml_root_not_dict(self, tmp_path):
        """ルートが辞書でないと CodeBookLoadError。"""
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(CodeBookLoadError, match="ルートが辞書ではありません"):
            load_codes(p)


# =============================================================================
# 異常系テスト: バリデーション（レベル2: 内容整合性）
# =============================================================================

class TestValidationErrors:
    """pydanticバリデーションエラーが正しく検出される。"""

    def test_missing_meta(self, tmp_yaml):
        """meta欠落で失敗。"""
        data = _minimal_valid_yaml_dict()
        del data["meta"]
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError):
            load_codes(path)

    def test_unknown_product_type(self, tmp_yaml):
        """未知の製品種別キーで失敗。"""
        data = _minimal_valid_yaml_dict()
        data["product_categories"]["UNKNOWN_PRODUCT"] = data["product_categories"]["ML"]
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError, match="未知の製品種別"):
            load_codes(path)

    def test_invalid_record_type(self, tmp_yaml):
        """不正なrecord_type値で失敗。"""
        data = _minimal_valid_yaml_dict()
        data["product_categories"]["ML"]["failure_categories"]["M001"]["record_type"] = "invalid_type"
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError):
            load_codes(path)

    def test_invalid_responsibility(self, tmp_yaml):
        """不正なresponsibility値で失敗。"""
        data = _minimal_valid_yaml_dict()
        data["product_categories"]["ML"]["failure_categories"]["M001"]["responsibility"] = "invalid"
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError):
            load_codes(path)

    def test_missing_special_codes(self, tmp_yaml):
        """特殊コード（is_special）が1つもないと失敗。"""
        data = _minimal_valid_yaml_dict()
        # ML側の特殊コードを全て通常コード扱いにする
        data["product_categories"]["ML"]["failure_categories"]["M_OTHER"]["is_special"] = False
        data["product_categories"]["ML"]["failure_categories"]["M_UNK"]["is_special"] = False
        # 加えて UNK の record_type も外してUNK消失させる
        data["product_categories"]["ML"]["failure_categories"]["M_UNK"]["record_type"] = "failure"
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError, match="特殊コード"):
            load_codes(path)

    def test_missing_unk_record_type(self, tmp_yaml):
        """record_type=unknown のコードが存在しないと失敗。"""
        data = _minimal_valid_yaml_dict()
        # M_UNKのrecord_typeを変える（is_specialは残す）
        data["product_categories"]["ML"]["failure_categories"]["M_UNK"]["record_type"] = "failure"
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError, match="判定不能コード"):
            load_codes(path)

    def test_missing_environment_none(self, tmp_yaml):
        """環境要因に none が欠けていると失敗。"""
        data = _minimal_valid_yaml_dict()
        del data["environment_factors"]["none"]
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError, match="environment_factors"):
            load_codes(path)

    def test_missing_environment_unknown(self, tmp_yaml):
        """環境要因に unknown が欠けていると失敗。"""
        data = _minimal_valid_yaml_dict()
        del data["environment_factors"]["unknown"]
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError, match="environment_factors"):
            load_codes(path)

    def test_missing_reproduction_status(self, tmp_yaml):
        """再現状況に必須キーが欠けていると失敗。"""
        data = _minimal_valid_yaml_dict()
        del data["reproduction_statuses"]["reproduced"]
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError, match="reproduction_statuses"):
            load_codes(path)

    def test_empty_failure_categories(self, tmp_yaml):
        """failure_categories が空辞書だと失敗（min_length=1）。"""
        data = _minimal_valid_yaml_dict()
        data["product_categories"]["ML"]["failure_categories"] = {}
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError):
            load_codes(path)

    def test_missing_required_field(self, tmp_yaml):
        """description が欠けていると失敗。"""
        data = _minimal_valid_yaml_dict()
        del data["product_categories"]["ML"]["failure_categories"]["M001"]["description"]
        path = tmp_yaml(data)
        with pytest.raises(CodeBookLoadError):
            load_codes(path)


# =============================================================================
# ヘルパー関数テスト
# =============================================================================

class TestHelpers:
    """ヘルパー関数の正常動作確認。"""

    @pytest.fixture(scope="class")
    def codebook(self):
        return load_codes(REAL_YAML_PATH)

    def test_get_failure_codes_for_product_ml(self, codebook):
        codes = codebook.get_failure_codes_for_product("ML")
        assert "M001" in codes
        assert "M_UNK" in codes
        assert "L001" not in codes  # LENSのコードは含まれない

    def test_get_failure_codes_for_product_lens(self, codebook):
        codes = codebook.get_failure_codes_for_product("LENS")
        assert "L001" in codes
        assert "M001" not in codes

    def test_get_failure_codes_with_enum(self, codebook):
        """ProductType Enum を渡してもOK。"""
        codes = codebook.get_failure_codes_for_product(ProductType.ML)
        assert "M001" in codes

    def test_get_failure_codes_invalid_product(self, codebook):
        with pytest.raises(ValueError, match="未知の製品種別"):
            codebook.get_failure_codes_for_product("UNKNOWN")

    def test_is_valid_failure_code(self, codebook):
        # ML側
        assert codebook.is_valid_failure_code("M001", "ML") is True
        assert codebook.is_valid_failure_code("M999", "ML") is False
        # LENS側
        assert codebook.is_valid_failure_code("L001", "LENS") is True
        # 製品違い
        assert codebook.is_valid_failure_code("M001", "LENS") is False
        assert codebook.is_valid_failure_code("L001", "ML") is False

    def test_is_manufacturer_responsibility(self, codebook):
        # M012（センサー内ゴミ）= manufacturer
        assert codebook.is_manufacturer_responsibility("M012", "ML") is True
        # M013（センサーゴミ）= user_or_unknown
        assert codebook.is_manufacturer_responsibility("M013", "ML") is False
        # M014（ファインダー内ゴミ）= manufacturer
        assert codebook.is_manufacturer_responsibility("M014", "ML") is True
        # M001（電源不良）= manufacturer（v0.2 で全コードに responsibility 付与）
        assert codebook.is_manufacturer_responsibility("M001", "ML") is True
        # M042（検査）= user_or_unknown（サービスレコード）
        assert codebook.is_manufacturer_responsibility("M042", "ML") is False
        # L031（カビ）= user_or_unknown（保管環境起因）
        assert codebook.is_manufacturer_responsibility("L031", "LENS") is False
        # 存在しないコード
        assert codebook.is_manufacturer_responsibility("M999", "ML") is False

    def test_is_service_record(self, codebook):
        # M042（検査）= service
        assert codebook.is_service_record("M042", "ML") is True
        # M043（クリーニング）= service
        assert codebook.is_service_record("M043", "ML") is True
        # M001（電源不良）= failure
        assert codebook.is_service_record("M001", "ML") is False
        # LENS側のサービスレコード
        assert codebook.is_service_record("L032", "LENS") is True
        # 存在しないコード
        assert codebook.is_service_record("M999", "ML") is False


# =============================================================================
# Enum値の整合性チェック
# =============================================================================

class TestEnumConsistency:
    """Enum値が期待通りであることを確認。"""

    def test_product_type_values(self):
        assert {p.value for p in ProductType} == {"ML", "LENS"}

    def test_record_type_values(self):
        assert {r.value for r in RecordType} == {"failure", "service", "unknown"}

    def test_responsibility_values(self):
        assert {r.value for r in Responsibility} == {"manufacturer", "user_or_unknown"}

    def test_description_status_values(self):
        assert {d.value for d in DescriptionStatus} == {"ai_inferred", "verified", "draft"}

    def test_env_source_values(self):
        assert {e.value for e in EnvSource} == {"user", "repair", "both"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
