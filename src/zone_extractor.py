"""
zone_extractor.py
=================
コメントテキストを4ゾーンに分解し、各ゾーンを適切にクリーニングする。

ゾーン定義:
    [preamble][bracket_prefix][marker_zone][postamble]

    preamble       : マーカー前のフリーテキスト（【】以外）
    bracket_prefix : マーカー前の【】で囲まれたテキスト（複数可）
    marker_zone    : 最初のマーカーから最後のマーカーチャンクまでの本文
    postamble      : 最後のマーカー以降の後置き情報（※、🔳、▪️等で始まる）

責務:
    - テキストの構造解析（4ゾーン分割）
    - ゾーンごとのクリーニング
    - マーカー位置の検出（連続マーカーのグルーピング含む）

zone_extractor は分割（レコード生成）には関与しない。
分割ロジックは split.py で担当する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# =============================================================================
# 正規表現パターン
# =============================================================================

# 全角丸数字
PATTERN_PRIMARY = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")

CIRCLED_TO_INT = {
    "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5,
    "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9, "⑩": 10,
}

# フォールバックパターン（半角番号、ピリオド付き等）
PATTERN_FALLBACK = re.compile(
    r"(?:^|\n)\s*(?:[(（]\s*\d+\s*[)）]|\d+\s*[\.\)、])"
)

# 【...】を抽出するパターン（最短マッチ）
PATTERN_BRACKETS = re.compile(r"【[^】]*】")

# 後置き情報の開始記号
# ※ ▪ ▪️ 🔳 ◆ ■ など、修理者・ユーザがコメントで使う記号
# 厳しめにすると後置きを取りこぼす、緩めると本文を削ってしまうのでバランス重要
POSTAMBLE_MARKER_CHARS = "※▪🔳◆■◇□"
PATTERN_POSTAMBLE_START = re.compile(rf"[{POSTAMBLE_MARKER_CHARS}]")


# =============================================================================
# データクラス
# =============================================================================

@dataclass
class MarkerOccurrence:
    """マーカー1個の出現情報。"""
    position: int        # 元テキスト内の位置
    marker_int: int      # 番号（1-10）


@dataclass
class MarkerGroup:
    """
    連続するマーカーのグループ。同じチャンクを複数sub_idに割り当てる単位。

    chunk_text はクリーニング後の最終テキストを保持する。
    位置情報（chunk_start/chunk_end）はデバッグ・検証用に元テキスト基準で保持。
    """
    marker_ints: list[int]      # グループに含まれる番号リスト（昇順）
    chunk_text: str = ""         # 最終的なテキストチャンク（クリーニング済み、マーカー除去済み）
    chunk_start: int = 0         # 元テキスト内のチャンク開始位置（デバッグ用）
    chunk_end: int = 0           # 元テキスト内のチャンク終了位置（デバッグ用）


@dataclass
class ZoneResult:
    """4ゾーン分解の結果。"""
    preamble: str = ""           # マーカー前のフリーテキスト（クリーニング済み）
    bracket_prefix: str = ""     # マーカー前の【】まとめ（クリーニング済み）
    marker_zone: str = ""        # マーカーゾーン本文（【】等は既に処理済み）
    postamble: str = ""          # 後置き情報

    # マーカー解析結果
    marker_occurrences: list[MarkerOccurrence] = field(default_factory=list)
    marker_groups: list[MarkerGroup] = field(default_factory=list)

    # 検出フラグ
    has_fallback_pattern: bool = False    # 半角番号等を検出
    has_duplicate_markers: bool = False   # 同じ番号が複数回出現

    def has_markers(self) -> bool:
        """マーカーが1つ以上存在するか。"""
        return len(self.marker_occurrences) > 0

    def unique_marker_set(self) -> set[int]:
        """ユニークな番号セット。"""
        return {m.marker_int for m in self.marker_occurrences}


# =============================================================================
# 補助関数
# =============================================================================

def _safe_str(value) -> str:
    """NaN/Noneを空文字に正規化。"""
    if value is None:
        return ""
    try:
        import math
        if isinstance(value, float) and math.isnan(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def detect_fallback(text: str) -> bool:
    """フォールバックパターンの存在をチェック。"""
    if not text:
        return False
    return bool(PATTERN_FALLBACK.search(text))


def find_marker_occurrences(text: str) -> list[MarkerOccurrence]:
    """テキスト中の全マーカー出現位置を返す（昇順）。"""
    if not text:
        return []
    occurrences = []
    for m in PATTERN_PRIMARY.finditer(text):
        char = m.group(0)
        occurrences.append(MarkerOccurrence(
            position=m.start(),
            marker_int=CIRCLED_TO_INT[char],
        ))
    return occurrences


def detect_duplicate_markers(occurrences: list[MarkerOccurrence]) -> bool:
    """同じ番号が複数回出現するかを判定。"""
    seen = set()
    for occ in occurrences:
        if occ.marker_int in seen:
            return True
        seen.add(occ.marker_int)
    return False


# =============================================================================
# ゾーン分割の中核
# =============================================================================

def _find_first_marker_position(text: str) -> int | None:
    """最初のマーカーの位置を返す。なければ None。"""
    m = PATTERN_PRIMARY.search(text)
    return m.start() if m else None


def _find_postamble_start_position(text: str, last_marker_pos: int) -> int | None:
    """
    最後のマーカー以降のテキストから、postamble開始位置を探す。

    Args:
        text: 元テキスト
        last_marker_pos: 最後のマーカーの位置

    Returns:
        postamble開始位置（テキスト全体の絶対位置）、なければ None
    """
    # 最後のマーカー以降の部分文字列を検索
    after_last = text[last_marker_pos:]
    # 最初のマーカー文字をスキップして本文に入る
    if not after_last:
        return None
    body_start_in_after = 1  # ①の次の文字から
    body = after_last[body_start_in_after:]

    m = PATTERN_POSTAMBLE_START.search(body)
    if m is None:
        return None
    return last_marker_pos + body_start_in_after + m.start()


def _split_preamble_and_brackets(text_before_marker: str) -> tuple[str, str]:
    """
    マーカー前のテキストを「フリーテキスト部分」と「【】まとめ」に分離。

    Args:
        text_before_marker: マーカー前の全テキスト

    Returns:
        (preamble, bracket_prefix)
        preamble: 【】を除去したフリーテキスト
        bracket_prefix: 【】を全部連結したもの
    """
    if not text_before_marker:
        return "", ""

    # 【】を抽出
    brackets = PATTERN_BRACKETS.findall(text_before_marker)
    bracket_prefix = "".join(brackets)

    # 【】を除去したフリーテキスト
    preamble = PATTERN_BRACKETS.sub("", text_before_marker).strip()

    return preamble, bracket_prefix


def _clean_marker_zone(marker_zone_text: str, is_user: bool) -> str:
    """
    マーカーゾーン内のテキストをクリーニング。

    ユーザコメント: ゾーン内の【】を除去（ノイズ扱い）
    修理コメント: ゾーン内の【】も保持（修理部品情報の可能性）

    Note:
        現在の運用方針（Q4回答）に基づき、ユーザコメントは【】除去のみ実施。
        将来メタデータ抽出が必要になった場合は、ここで処理を追加できる。
    """
    if is_user:
        return PATTERN_BRACKETS.sub("", marker_zone_text)
    return marker_zone_text


def _build_marker_groups(
    cleaned_marker_zone: str,
) -> list[MarkerGroup]:
    """
    クリーニング後のマーカーゾーン本文から、連続マーカーグループのリストを構築する。

    アルゴリズム:
        1. クリーニング後のテキストでマーカー位置を再検出
        2. 隣接するマーカーをグループ化（間に他の文字がない場合）
        3. 各グループに対し、次のグループ直前までのテキストを割り当てる

    Args:
        cleaned_marker_zone: 既にクリーニング済みのマーカーゾーン本文

    Returns:
        MarkerGroup のリスト（位置順）。各グループの chunk_text にチャンクが入る。
    """
    if not cleaned_marker_zone:
        return []

    occurrences = find_marker_occurrences(cleaned_marker_zone)
    if not occurrences:
        return []

    sorted_occ = sorted(occurrences, key=lambda x: x.position)

    # 連続マーカーをグループ化
    # 全角丸数字は1文字なので、prev.position+1 == curr.position なら連続
    groups_raw: list[list[MarkerOccurrence]] = [[sorted_occ[0]]]
    for i in range(1, len(sorted_occ)):
        prev = sorted_occ[i - 1]
        curr = sorted_occ[i]
        if curr.position == prev.position + 1:
            groups_raw[-1].append(curr)
        else:
            groups_raw.append([curr])

    # 各グループにチャンク範囲を割り当て
    groups: list[MarkerGroup] = []
    for i, grp in enumerate(groups_raw):
        chunk_start = grp[0].position
        if i + 1 < len(groups_raw):
            chunk_end = groups_raw[i + 1][0].position
        else:
            chunk_end = len(cleaned_marker_zone)

        # チャンクテキストを抽出（マーカー連続部分をスキップ）
        raw_chunk = cleaned_marker_zone[chunk_start:chunk_end]
        idx = 0
        while idx < len(raw_chunk) and raw_chunk[idx] in CIRCLED_TO_INT:
            idx += 1
        chunk_text = raw_chunk[idx:].strip()

        groups.append(MarkerGroup(
            marker_ints=[occ.marker_int for occ in grp],
            chunk_text=chunk_text,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
        ))

    return groups


# =============================================================================
# メインAPI
# =============================================================================

def extract_zones(text: str, is_user: bool) -> ZoneResult:
    """
    テキストを4ゾーンに分解する。

    Args:
        text: 入力テキスト（NaN/None も許容）
        is_user: True ならユーザコメント、False なら修理コメント。
                 ゾーン処理ルールが異なる:
                 - ユーザ: マーカー前【】は除去、マーカーゾーン内【】も除去
                 - 修理者: マーカー前【】はbracket_prefixとして保持、
                          マーカーゾーン内【】は本文として保持

    Returns:
        ZoneResult: 4ゾーン分解結果とマーカー解析情報
    """
    text = _safe_str(text)
    result = ZoneResult()

    if not text:
        return result

    # フォールバックパターン検出
    result.has_fallback_pattern = detect_fallback(text)

    # マーカー出現を取得
    occurrences = find_marker_occurrences(text)
    result.marker_occurrences = occurrences

    # マーカーがない場合: 全文がpreamble扱い（【】処理は実施）
    if not occurrences:
        if is_user:
            # ユーザコメントは【】除去
            preamble, bracket_prefix = _split_preamble_and_brackets(text)
            result.preamble = preamble
            # bracket_prefixはユーザの場合は破棄（情報整理のため）
            # ただしフリーテキストには既に含まれていない
        else:
            # 修理コメントは【】保持
            result.preamble = text  # そのまま
        return result

    # 重複マーカー検出（後段で利用）
    result.has_duplicate_markers = detect_duplicate_markers(occurrences)

    # マーカー前のテキスト（最初のマーカー位置まで）
    first_marker_pos = occurrences[0].position
    text_before = text[:first_marker_pos]

    if is_user:
        # ユーザ: 【】除去してpreambleに
        preamble, _bracket = _split_preamble_and_brackets(text_before)
        result.preamble = preamble
        # bracket_prefix は破棄
    else:
        # 修理者: preamble + bracket_prefix を分けて保持
        preamble, bracket_prefix = _split_preamble_and_brackets(text_before)
        result.preamble = preamble
        result.bracket_prefix = bracket_prefix

    # 最後のマーカーの位置
    last_marker_pos = max(occ.position for occ in occurrences)

    # postamble開始位置の検出
    postamble_start = _find_postamble_start_position(text, last_marker_pos)

    if postamble_start is not None:
        zone_end = postamble_start
        result.postamble = text[postamble_start:].strip()
    else:
        zone_end = len(text)
        result.postamble = ""

    # マーカーゾーン本文を抽出してクリーニング
    marker_zone_raw = text[first_marker_pos:zone_end]
    result.marker_zone = _clean_marker_zone(marker_zone_raw, is_user).strip()

    # マーカーグループ構築（クリーニング後のテキスト基準）
    result.marker_groups = _build_marker_groups(result.marker_zone)

    return result
