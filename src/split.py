"""
split.py
========
修理データのコメント分割処理（v2: 4ゾーン対応）。

ユーザコメント・修理者コメントを4ゾーン（preamble/bracket_prefix/marker_zone/postamble）
に分解した上で、故障現象ごとに分割して (repair_id, sub_id) を主キーとするレコードを生成。

責務:
    - zone_extractor を使った構造解析
    - 番号セットの一致確認
    - 連続マーカーグループの sub_id 展開
    - 内部コメント[I1][I2]の各分割単位への複製
    - SplitReport で集計指標を返す

分割しないケース（1レコード扱い）:
    - 両方ともマーカーなし
    - どちらか片方がマーカー1つ以下
    - ユーザと修理者の番号セット不一致
    - 文中にマーカー重複（同じ番号が複数回出現）

Usage:
    from split import split_records, SplitConfig

    config = SplitConfig()
    split_df, report = split_records(input_df, config)
    print(report.summary())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from zone_extractor import ZoneResult, extract_zones

logger = logging.getLogger(__name__)


# =============================================================================
# 設定クラス
# =============================================================================

@dataclass
class ColumnMapping:
    """入力DataFrameのカラム名マッピング。"""
    repair_id: str = "repair_id"
    product_type: str = "product_type"
    user_comment: str = "user_comment"
    repair_comment: str = "repair_comment"
    internal_1: str = "internal_1"
    internal_2: str = "internal_2"

    def required_columns(self) -> list[str]:
        return [
            self.repair_id, self.product_type,
            self.user_comment, self.repair_comment,
            self.internal_1, self.internal_2,
        ]


@dataclass
class SplitConfig:
    """分割処理の設定。"""
    columns: ColumnMapping = field(default_factory=ColumnMapping)
    on_fallback_pattern: Literal["warn", "silent"] = "warn"
    on_marker_mismatch: Literal["warn", "silent"] = "warn"
    on_duplicate_markers: Literal["warn", "silent"] = "warn"
    duplicate_internal_to_all: bool = True
    abnormal_split_threshold: int = 7


# =============================================================================
# 出力カラム名（固定）
# =============================================================================

OUT_REPAIR_ID = "repair_id"
OUT_SUB_ID = "sub_id"
OUT_PRODUCT_TYPE = "product_type"
OUT_USER_TEXT = "user_text"
OUT_USER_CONTEXT = "user_context"          # preamble（ユーザは【】除去後）
OUT_USER_POSTAMBLE = "user_postamble"      # ※以降の後置き情報
OUT_REPAIR_TEXT = "repair_text"
OUT_REPAIR_CONTEXT = "repair_context"      # preamble + bracket_prefix（修理者は【】保持）
OUT_REPAIR_POSTAMBLE = "repair_postamble"
OUT_INTERNAL_1 = "internal_1"
OUT_INTERNAL_2 = "internal_2"
OUT_SPLIT_INFO = "split_info"

OUTPUT_COLUMNS = [
    OUT_REPAIR_ID, OUT_SUB_ID, OUT_PRODUCT_TYPE,
    OUT_USER_TEXT, OUT_USER_CONTEXT, OUT_USER_POSTAMBLE,
    OUT_REPAIR_TEXT, OUT_REPAIR_CONTEXT, OUT_REPAIR_POSTAMBLE,
    OUT_INTERNAL_1, OUT_INTERNAL_2, OUT_SPLIT_INFO,
]


# =============================================================================
# レポート用のデータ構造
# =============================================================================

@dataclass
class SplitReport:
    """分割処理の集計レポート。"""
    total_input_records: int = 0
    total_output_records: int = 0
    no_split_count: int = 0
    split_count: int = 0
    marker_mismatch_count: int = 0
    duplicate_marker_count: int = 0      # 重複マーカーで不分割
    fallback_detected_count: int = 0
    abnormal_split_count: int = 0
    empty_record_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 50,
            "分割処理レポート",
            "=" * 50,
            f"入力レコード数        : {self.total_input_records}",
            f"出力レコード数        : {self.total_output_records}",
            f"  分割なし            : {self.no_split_count}",
            f"  分割あり            : {self.split_count}",
            f"  番号不一致（不分割） : {self.marker_mismatch_count}",
            f"  重複マーカー（不分割）: {self.duplicate_marker_count}",
            f"  フォールバック検出   : {self.fallback_detected_count}",
            f"  異常分割数(警告)    : {self.abnormal_split_count}",
            f"  全カラム空           : {self.empty_record_count}",
            f"警告総数              : {len(self.warnings)}",
            "=" * 50,
        ]
        return "\n".join(lines)


# =============================================================================
# 補助関数
# =============================================================================

def _safe_str(value) -> str:
    """NaN/Noneを空文字に正規化。"""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _build_context(zones: ZoneResult) -> str:
    """preamble + bracket_prefix を結合してcontext文字列を生成。"""
    parts = []
    if zones.preamble:
        parts.append(zones.preamble)
    if zones.bracket_prefix:
        parts.append(zones.bracket_prefix)
    return " ".join(parts).strip()


def _build_no_split_record(
    row: pd.Series,
    config: SplitConfig,
    user_zones: ZoneResult,
    repair_zones: ZoneResult,
    split_info: str,
) -> dict:
    """
    分割なし時の単一レコードを構築。

    マーカーがある場合: marker_zone を本文、preamble + bracket_prefix を context
    マーカーがない場合: preamble を本文、context は空
    """
    cm = config.columns

    # ユーザ側
    if user_zones.has_markers():
        user_text = user_zones.marker_zone
        user_context = _build_context(user_zones)
    else:
        user_text = user_zones.preamble
        user_context = ""

    # 修理者側
    if repair_zones.has_markers():
        repair_text = repair_zones.marker_zone
        repair_context = _build_context(repair_zones)
    else:
        repair_text = repair_zones.preamble
        repair_context = ""

    return {
        OUT_REPAIR_ID: row[cm.repair_id],
        OUT_SUB_ID: 1,
        OUT_PRODUCT_TYPE: row[cm.product_type],
        OUT_USER_TEXT: user_text,
        OUT_USER_CONTEXT: user_context,
        OUT_USER_POSTAMBLE: user_zones.postamble,
        OUT_REPAIR_TEXT: repair_text,
        OUT_REPAIR_CONTEXT: repair_context,
        OUT_REPAIR_POSTAMBLE: repair_zones.postamble,
        OUT_INTERNAL_1: _safe_str(row[cm.internal_1]),
        OUT_INTERNAL_2: _safe_str(row[cm.internal_2]),
        OUT_SPLIT_INFO: split_info,
    }


def _expand_groups_to_marker_chunks(zones: ZoneResult) -> dict[int, str]:
    """
    マーカーグループを展開して {marker_int: chunk_text} 辞書を返す。

    連続マーカー [1,2] のグループは、{1: chunk, 2: chunk} のように
    同じテキストを各番号に複製する。
    """
    result: dict[int, str] = {}
    for group in zones.marker_groups:
        for marker_int in group.marker_ints:
            result[marker_int] = group.chunk_text
    return result


def _add_warning(
    report: SplitReport,
    on_action: Literal["warn", "silent"],
    msg: str,
) -> None:
    """警告メッセージをレポートに追加（silent モードでは追加しない）。"""
    if on_action == "warn":
        report.warnings.append(msg)
        logger.warning(msg)


# =============================================================================
# 1レコードの分割処理
# =============================================================================

def _split_single_record(
    row: pd.Series,
    config: SplitConfig,
    report: SplitReport,
) -> list[dict]:
    """1レコードを分割処理し、出力用レコードのリストを返す。"""
    cm = config.columns
    repair_id = row[cm.repair_id]
    user_raw = _safe_str(row[cm.user_comment])
    repair_raw = _safe_str(row[cm.repair_comment])

    # ケース: 両方とも空
    if not user_raw and not repair_raw:
        report.empty_record_count += 1
        empty_zones = ZoneResult()
        return [_build_no_split_record(row, config, empty_zones, empty_zones, "empty")]

    # 4ゾーン分解
    user_zones = extract_zones(user_raw, is_user=True)
    repair_zones = extract_zones(repair_raw, is_user=False)

    # フォールバックパターン警告
    if user_zones.has_fallback_pattern or repair_zones.has_fallback_pattern:
        report.fallback_detected_count += 1
        _add_warning(
            report, config.on_fallback_pattern,
            f"[fallback_detected] repair_id={repair_id}: "
            f"全角丸数字以外の番号表記を検出 "
            f"(user={user_zones.has_fallback_pattern}, "
            f"repair={repair_zones.has_fallback_pattern})"
        )

    # 重複マーカー検出 → 不分割
    if user_zones.has_duplicate_markers or repair_zones.has_duplicate_markers:
        report.duplicate_marker_count += 1
        _add_warning(
            report, config.on_duplicate_markers,
            f"[duplicate_markers] repair_id={repair_id}: "
            f"同じ番号が複数回出現 "
            f"(user={user_zones.has_duplicate_markers}, "
            f"repair={repair_zones.has_duplicate_markers}) "
            f"→ 分割せず1レコードとして処理"
        )
        return [_build_no_split_record(
            row, config, user_zones, repair_zones, "duplicate_markers"
        )]

    # マーカー数チェック
    user_marker_set = user_zones.unique_marker_set()
    repair_marker_set = repair_zones.unique_marker_set()

    has_enough_user = len(user_marker_set) >= 2
    has_enough_repair = len(repair_marker_set) >= 2

    if not has_enough_user and not has_enough_repair:
        # 番号がない or 番号が1個だけ → 分割なし
        report.no_split_count += 1
        return [_build_no_split_record(
            row, config, user_zones, repair_zones, "no_markers"
        )]

    # 番号セット一致確認
    if user_marker_set != repair_marker_set:
        report.marker_mismatch_count += 1
        _add_warning(
            report, config.on_marker_mismatch,
            f"[marker_mismatch] repair_id={repair_id}: "
            f"ユーザと修理者の番号セットが不一致 "
            f"(user={sorted(user_marker_set)}, repair={sorted(repair_marker_set)}) "
            f"→ 分割せず1レコードとして処理"
        )
        return [_build_no_split_record(
            row, config, user_zones, repair_zones, "marker_mismatch"
        )]

    # 番号セット一致かつ2個以上 → 分割実施
    user_chunks = _expand_groups_to_marker_chunks(user_zones)
    repair_chunks = _expand_groups_to_marker_chunks(repair_zones)

    sorted_marker_ints = sorted(user_marker_set)
    n_splits = len(sorted_marker_ints)

    # 異常分割数の警告（warn固定: 重大なので）
    if n_splits > config.abnormal_split_threshold:
        report.abnormal_split_count += 1
        msg = (
            f"[abnormal_split] repair_id={repair_id}: "
            f"分割数が閾値超過 ({n_splits} > {config.abnormal_split_threshold})"
        )
        report.warnings.append(msg)
        logger.warning(msg)

    # context は全sub_idで共通
    user_context = _build_context(user_zones)
    repair_context = _build_context(repair_zones)

    report.split_count += 1
    out_records = []
    for sub_id, marker_int in enumerate(sorted_marker_ints, start=1):
        if config.duplicate_internal_to_all or sub_id == 1:
            i1 = _safe_str(row[cm.internal_1])
            i2 = _safe_str(row[cm.internal_2])
        else:
            i1 = ""
            i2 = ""

        out_records.append({
            OUT_REPAIR_ID: repair_id,
            OUT_SUB_ID: sub_id,
            OUT_PRODUCT_TYPE: row[cm.product_type],
            OUT_USER_TEXT: user_chunks.get(marker_int, ""),
            OUT_USER_CONTEXT: user_context,
            OUT_USER_POSTAMBLE: user_zones.postamble,
            OUT_REPAIR_TEXT: repair_chunks.get(marker_int, ""),
            OUT_REPAIR_CONTEXT: repair_context,
            OUT_REPAIR_POSTAMBLE: repair_zones.postamble,
            OUT_INTERNAL_1: i1,
            OUT_INTERNAL_2: i2,
            OUT_SPLIT_INFO: f"split:{n_splits}",
        })

    return out_records


# =============================================================================
# メイン関数
# =============================================================================

def validate_input_columns(df: pd.DataFrame, config: SplitConfig) -> None:
    """入力DataFrameに必須カラムが揃っているかチェック。"""
    required = config.columns.required_columns()
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"入力DataFrameに必須カラムが不足: {missing}. "
            f"ColumnMappingを調整してください。"
        )


def split_records(
    df: pd.DataFrame,
    config: SplitConfig | None = None,
) -> tuple[pd.DataFrame, SplitReport]:
    """
    修理データDataFrameを①②③で分割し、(repair_id, sub_id)単位のDataFrameを返す。

    Args:
        df: 入力DataFrame
        config: 分割設定（Noneでデフォルト）

    Returns:
        (split_df, report) のタプル

    Raises:
        ValueError: 必須カラムが不足している場合
    """
    if config is None:
        config = SplitConfig()

    validate_input_columns(df, config)

    report = SplitReport(total_input_records=len(df))
    out_records: list[dict] = []

    for _, row in df.iterrows():
        records = _split_single_record(row, config, report)
        out_records.extend(records)

    report.total_output_records = len(out_records)

    if not out_records:
        out_df = pd.DataFrame(columns=OUTPUT_COLUMNS)
    else:
        out_df = pd.DataFrame(out_records)

    return out_df, report
