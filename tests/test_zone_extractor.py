"""
test_zone_extractor.py
======================
zone_extractor.py の単体テスト。

ゾーン分解と各ゾーンのクリーニングを検証する。
実データ（R001〜R018）のパターンを網羅。
"""

from __future__ import annotations

import pytest

from zone_extractor import (
    ZoneResult,
    detect_duplicate_markers,
    detect_fallback,
    extract_zones,
    find_marker_occurrences,
)


# =============================================================================
# 低レベル関数のテスト
# =============================================================================

class TestFindMarkerOccurrences:
    def test_no_markers(self):
        assert find_marker_occurrences("普通のテキスト") == []

    def test_single_marker(self):
        result = find_marker_occurrences("①AF")
        assert len(result) == 1
        assert result[0].position == 0
        assert result[0].marker_int == 1

    def test_multiple_markers(self):
        result = find_marker_occurrences("①A ②B ③C")
        markers = [r.marker_int for r in result]
        assert markers == [1, 2, 3]

    def test_consecutive_markers(self):
        result = find_marker_occurrences("①②③")
        positions = [r.position for r in result]
        assert positions == [0, 1, 2]

    def test_empty_string(self):
        assert find_marker_occurrences("") == []


class TestDetectDuplicateMarkers:
    def test_no_duplicate(self):
        from zone_extractor import MarkerOccurrence
        occs = [MarkerOccurrence(0, 1), MarkerOccurrence(5, 2)]
        assert detect_duplicate_markers(occs) is False

    def test_with_duplicate(self):
        from zone_extractor import MarkerOccurrence
        occs = [MarkerOccurrence(0, 1), MarkerOccurrence(5, 2), MarkerOccurrence(20, 1)]
        assert detect_duplicate_markers(occs) is True


class TestDetectFallback:
    def test_no_fallback(self):
        assert detect_fallback("普通のテキスト①故障") is False

    def test_paren_fallback(self):
        assert detect_fallback("(1)A (2)B") is True

    def test_period_fallback(self):
        assert detect_fallback("1. A\n2. B") is True

    def test_only_circled(self):
        assert detect_fallback("①A ②B") is False


# =============================================================================
# extract_zones の主要ケース
# =============================================================================

class TestExtractZonesBasic:
    """通常ケース。"""

    def test_no_markers_user(self):
        r = extract_zones("ピントが合わない", is_user=True)
        assert r.preamble == "ピントが合わない"
        assert r.bracket_prefix == ""
        assert r.marker_zone == ""
        assert r.postamble == ""
        assert not r.has_markers()

    def test_no_markers_repair(self):
        r = extract_zones("AFユニット交換にて復旧", is_user=False)
        assert r.preamble == "AFユニット交換にて復旧"
        assert not r.has_markers()

    def test_simple_split(self):
        r = extract_zones("①AF ②電源", is_user=True)
        assert r.preamble == ""
        assert r.marker_zone == "①AF ②電源"
        assert r.has_markers()
        assert r.unique_marker_set() == {1, 2}
        assert len(r.marker_groups) == 2
        assert r.marker_groups[0].chunk_text == "AF"
        assert r.marker_groups[1].chunk_text == "電源"

    def test_empty_input(self):
        r = extract_zones("", is_user=True)
        assert r.preamble == ""
        assert not r.has_markers()


class TestExtractZonesPreamble:
    """preamble（マーカー前フリーテキスト）の処理。"""

    def test_user_preamble_with_brackets(self):
        """ユーザコメントの前置き【】は除去される (R009)."""
        text = "【前回履歴】H111①AF ②電源"
        r = extract_zones(text, is_user=True)
        assert r.preamble == "H111"
        assert r.bracket_prefix == ""  # ユーザは破棄

    def test_repair_preamble_with_brackets(self):
        """修理者コメントの前置き【】は保持される。"""
        text = "【保証適用】①AF調整 ②電池清掃"
        r = extract_zones(text, is_user=False)
        assert r.preamble == ""
        assert r.bracket_prefix == "【保証適用】"

    def test_user_freeform_preamble(self):
        """フリーテキストのpreamble (R012)."""
        text = "フリーズ・エラー70・ブラックアウト①エラー ②破損"
        r = extract_zones(text, is_user=True)
        assert r.preamble == "フリーズ・エラー70・ブラックアウト"
        assert r.marker_zone == "①エラー ②破損"

    def test_repair_freeform_preamble(self):
        """修理者のフリーテキストpreamble (R013)."""
        text = "オーバーホールを実施いたしました ①現象確認できず ②破損確認"
        r = extract_zones(text, is_user=False)
        assert r.preamble == "オーバーホールを実施いたしました"
        assert r.marker_zone == "①現象確認できず ②破損確認"


class TestExtractZonesPostamble:
    """postamble（後置き情報）の処理。"""

    def test_user_postamble_circle(self):
        """🔳, ▪️ などで始まる後置き。"""
        text = "①AF ②電源 ▪️同時預かり"
        r = extract_zones(text, is_user=True)
        assert r.marker_zone == "①AF ②電源"
        assert "同時預かり" in r.postamble

    def test_postamble_asterisk(self):
        """※で始まる後置き (R011)."""
        text = "①AF調整 ②電池清掃 ※同時預かり"
        r = extract_zones(text, is_user=False)
        assert r.marker_zone == "①AF調整 ②電池清掃"
        assert "同時預かり" in r.postamble

    def test_no_postamble(self):
        """後置きなしの場合は空文字。"""
        text = "①AF ②電源"
        r = extract_zones(text, is_user=True)
        assert r.postamble == ""


class TestExtractZonesMarkerZoneCleaning:
    """マーカーゾーン内のクリーニング。"""

    def test_user_brackets_in_marker_zone_removed(self):
        """ユーザコメントの番号項目内【】は除去される (R014)."""
        text = "①AF 【ご指摘外の現象】②電源 【追加ご指摘】③シャッター"
        r = extract_zones(text, is_user=True)
        # クリーニング後は【】が消える
        assert "【" not in r.marker_zone
        assert "】" not in r.marker_zone
        # 各グループのチャンクも【】を含まない
        chunks = [g.chunk_text for g in r.marker_groups]
        assert chunks == ["AF", "電源", "シャッター"]

    def test_repair_brackets_in_marker_zone_kept(self):
        """修理者コメントの番号項目内【】は保持される。"""
        text = "①AF調整 【部品X使用】②電池清掃"
        r = extract_zones(text, is_user=False)
        # 【】が残っている
        assert "【部品X使用】" in r.marker_zone


class TestExtractZonesGroupedMarkers:
    """連続マーカーグループの処理。"""

    def test_consecutive_pair(self):
        """①②まとめ回答 (R015)."""
        text = "①②ご指摘の現象確認"
        r = extract_zones(text, is_user=False)
        assert len(r.marker_groups) == 1
        assert r.marker_groups[0].marker_ints == [1, 2]
        assert r.marker_groups[0].chunk_text == "ご指摘の現象確認"

    def test_consecutive_and_single_mix(self):
        """①②と③④⑤⑥⑦の混在 (R016)."""
        text = "①②レンズ側 ③④⑤⑥⑦指摘外"
        r = extract_zones(text, is_user=False)
        assert len(r.marker_groups) == 2
        assert r.marker_groups[0].marker_ints == [1, 2]
        assert r.marker_groups[0].chunk_text == "レンズ側"
        assert r.marker_groups[1].marker_ints == [3, 4, 5, 6, 7]
        assert r.marker_groups[1].chunk_text == "指摘外"

    def test_non_consecutive_individual(self):
        """間隔が空いている個別マーカー。"""
        text = "①AF ②電源 ③LCD"
        r = extract_zones(text, is_user=True)
        assert len(r.marker_groups) == 3
        for g in r.marker_groups:
            assert len(g.marker_ints) == 1


class TestExtractZonesDuplicateMarkers:
    """重複マーカー検出。"""

    def test_duplicate_markers_detected(self):
        """文中で同じ番号が複数回出現 (R018)."""
        text = "①②確認できず③により発生。③内部の腐食"
        r = extract_zones(text, is_user=False)
        assert r.has_duplicate_markers is True

    def test_no_duplicate_in_simple_case(self):
        text = "①AF ②電源 ③LCD"
        r = extract_zones(text, is_user=True)
        assert r.has_duplicate_markers is False


class TestExtractZonesRealDataIntegration:
    """実データ（R001〜R018）の統合テスト。"""

    def test_R009_user(self):
        """R009: 【前置】+ preamble + postamble."""
        text = "【前回履歴】H111①AF ②電源 ▪️同時預かり"
        r = extract_zones(text, is_user=True)
        assert r.preamble == "H111"
        assert r.marker_zone == "①AF ②電源"
        assert "同時預かり" in r.postamble

    def test_R012_user(self):
        """R012: フリーテキストpreamble."""
        text = "フリーズ・エラー70・ブラックアウト①エラー ②マウント板ばね破損"
        r = extract_zones(text, is_user=True)
        assert r.preamble == "フリーズ・エラー70・ブラックアウト"
        assert "①" in r.marker_zone

    def test_R013_user_brackets_dropped(self):
        """R013: ユーザの【】は破棄."""
        text = "【ショック品】【オーバホール】①エラー ②マウント板ばね破損"
        r = extract_zones(text, is_user=True)
        assert r.preamble == ""
        assert r.bracket_prefix == ""

    def test_R013_repair_preamble_kept(self):
        """R013: 修理者のpreamble (フリーテキスト) は保持."""
        text = "オーバーホールを実施いたしました ①現象確認できず ②破損確認"
        r = extract_zones(text, is_user=False)
        assert "オーバーホール" in r.preamble

    def test_R016_repair_grouped(self):
        """R016: 連続+単独混在."""
        text = "①②レンズ側にて対応 ③④⑤⑥⑦ご指摘外の現象"
        r = extract_zones(text, is_user=False)
        assert len(r.marker_groups) == 2
        assert r.marker_groups[0].marker_ints == [1, 2]
        assert r.marker_groups[1].marker_ints == [3, 4, 5, 6, 7]

    def test_R018_repair_duplicate(self):
        """R018: 重複マーカー検出."""
        text = "①②確認できず③により発生。③内部の腐食"
        r = extract_zones(text, is_user=False)
        assert r.has_duplicate_markers is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
