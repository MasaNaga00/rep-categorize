"""
test_codes_loader_additions.py
==============================
チャット3で codes_loader.py に追加した get_failure_category のテスト。

既存の tests/test_codes_loader.py を壊さないよう、追加分だけ独立ファイルにする。
本来は test_codes_loader.py に統合するのが望ましい（ユーザ判断）。
"""

from __future__ import annotations

import pytest

from codes_loader import FailureCategory, RecordType, Responsibility


class TestGetFailureCategory:
    """CodeBook.get_failure_category のテスト。"""

    def test_returns_failure_category_for_existing_ml_code(self, codebook):
        fc = codebook.get_failure_category("M001", "ML")
        assert fc is not None
        assert isinstance(fc, FailureCategory)
        assert fc.name

    def test_returns_failure_category_for_existing_lens_code(self, codebook):
        fc = codebook.get_failure_category("L001", "LENS")
        assert fc is not None
        assert isinstance(fc, FailureCategory)

    def test_returns_none_for_unknown_code(self, codebook):
        assert codebook.get_failure_category("M999", "ML") is None
        assert codebook.get_failure_category("L999", "LENS") is None

    def test_returns_none_for_wrong_product_code(self, codebook):
        assert codebook.get_failure_category("M001", "LENS") is None

    def test_raises_on_unknown_product_type(self, codebook):
        with pytest.raises(ValueError, match="未知の製品種別"):
            codebook.get_failure_category("M001", "UNKNOWN_PRODUCT")

    def test_attributes_accessible(self, codebook):
        fc = codebook.get_failure_category("M012", "ML")
        assert fc is not None
        assert fc.name
        assert fc.description
        assert fc.record_type == RecordType.FAILURE
        assert fc.responsibility == Responsibility.MANUFACTURER

    def test_service_record_has_service_type(self, codebook):
        fc = codebook.get_failure_category("M042", "ML")
        assert fc is not None
        assert fc.record_type == RecordType.SERVICE
