"""
output_formatter.py
===================
derive_metrics の出力 DataFrame を3形式の CSV に変換・書き出しする。

出力形式:
    1. ワイド形式  : (repair_id, sub_id) 単位の1行、Tableauダッシュボード用サマリ
    2. ロング形式  : environment_factors を縦展開、環境要因別ドリルダウン用
    3. 集約形式    : repair_id 単位、DB追記用

責務:
    - 既存の派生指標カラムから3形式に整形
    - UTF-8 (BOM 付き) で書き出し (Tableau Desktop の日本語対応)
    - dict / list カラムは表示用に文字列化
    - {timestamp}_output_meta.json も合わせて出力

Usage:
    from output_formatter import write_all_formats

    paths = write_all_formats(df, output_dir=Path("outputs/final"),
                              timestamp="20260513_120000", codebook=codebook)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from codes_loader import CodeBook

logger = logging.getLogger(__name__)


# =============================================================================
# 定数: カラム定義
# =============================================================================

# ワイド形式で出力するカラム順序 (列を絞らず全部出すが、表示順を統一)
_WIDE_COLUMN_ORDER: list[str] = [
    # 主キー / メタ
    "repair_id", "sub_id", "product_type",
    # 入力テキスト
    "user_text", "user_context",
    "repair_text", "repair_context",
    "internal_1", "internal_2",
    # ユーザ視点
    "user_failure_code", "user_failure_name",
    "user_confidence", "user_evidence", "user_insufficient_info",
    # 修理者視点
    "repair_failure_code", "repair_failure_name",
    "repair_confidence", "repair_evidence", "repair_insufficient_info",
    # 再現状況
    "reproduction_status", "reproduction_evidence", "reproduction_confidence",
    # 環境要因
    "environment_factors_str",
    "has_water", "has_sand_dust", "has_impact",
    "has_heat", "has_cold", "has_humidity",
    # 派生指標
    "perspective_match",
    "is_misjudged",
    "has_harsh_env",
    "has_repair_confirmed_env",
    "is_manufacturer_responsibility_user",
    "is_manufacturer_responsibility_repair",
    "is_service_record",
    "min_confidence",
]

# ロング形式のカラム
_LONG_COLUMN_ORDER: list[str] = [
    "repair_id", "sub_id", "product_type",
    "environment_factor",
    "environment_evidence_source",
    "environment_evidence",
    "environment_confidence",
    "user_failure_code", "user_failure_name",
    "repair_failure_code", "repair_failure_name",
    "reproduction_status",
]


# =============================================================================
# ワイド形式
# =============================================================================

def to_wide_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    ワイド形式 (Tableauダッシュボード用) に整形する。

    入力 df は build_derived_dataframe の出力相当を期待。
    既存カラムを必要な順に並び替え、dict 型カラムは含めない。

    Args:
        df: 派生指標計算済みの DataFrame

    Returns:
        ワイド形式 DataFrame (既存カラムから選抜)
    """
    cols = [c for c in _WIDE_COLUMN_ORDER if c in df.columns]
    # 念のため重複は排除
    seen: set[str] = set()
    ordered = []
    for c in cols:
        if c not in seen:
            ordered.append(c)
            seen.add(c)
    return df[ordered].copy()


# =============================================================================
# ロング形式
# =============================================================================

def to_long_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    ロング形式 (環境要因を縦展開) に変換する。

    各 (repair_id, sub_id) について、environment_factors の各要素を1行に展開する。
    environment_evidence_source / environment_evidence / environment_confidence は
    dict なので、各 environment_factor をキーとして対応する値を取り出す。

    例:
        wide: env_factors=["water","impact"], env_source={"water":"user","impact":"repair"}
        long: 2行 (water/user/..., impact/repair/...)

    環境要因が空リストの場合は1行も出力されない。

    Args:
        df: ワイド形式の DataFrame (environment_factors を含む)

    Returns:
        ロング形式 DataFrame
    """
    # 必要列のみで縮小コピー、explode の効率化
    needed = [
        "repair_id", "sub_id", "product_type",
        "environment_factors",
        "environment_evidence_source",
        "environment_evidence",
        "environment_confidence",
        "user_failure_code", "user_failure_name",
        "repair_failure_code", "repair_failure_name",
        "reproduction_status",
    ]
    present = [c for c in needed if c in df.columns]
    work = df[present].copy()

    # environment_factors が list でないものは [] にする (explode で消える)
    work["environment_factors"] = work["environment_factors"].apply(
        lambda v: v if isinstance(v, list) else []
    )
    work = work.explode("environment_factors", ignore_index=True)
    work = work.rename(columns={"environment_factors": "environment_factor"})

    # explode 後、空リストだった行は environment_factor=NaN になる → 除去
    work = work[work["environment_factor"].notna()].reset_index(drop=True)

    # dict から factor 名で対応する値を取り出す
    work["environment_evidence_source"] = work.apply(
        lambda r: _dict_lookup(r.get("environment_evidence_source"), r["environment_factor"]),
        axis=1,
    )
    work["environment_evidence"] = work.apply(
        lambda r: _dict_lookup(r.get("environment_evidence"), r["environment_factor"]),
        axis=1,
    )
    work["environment_confidence"] = work.apply(
        lambda r: _dict_lookup(r.get("environment_confidence"), r["environment_factor"]),
        axis=1,
    )

    cols = [c for c in _LONG_COLUMN_ORDER if c in work.columns]
    return work[cols]


def _dict_lookup(d: Any, key: str) -> Any:
    """dict から key の値を取り出す (None 安全)。"""
    if not isinstance(d, dict):
        return None
    return d.get(key)


# =============================================================================
# 集約形式
# =============================================================================

def to_aggregated_format(
    df: pd.DataFrame,
    codebook: CodeBook | None = None,  # noqa: ARG001  予約: 将来の利用に備えて受け取る
) -> pd.DataFrame:
    """
    repair_id 単位に集約する形式 (DB追記用)。

    Args:
        df: 派生指標計算済みの DataFrame
        codebook: 将来の拡張用 (現状は未使用、シグネチャ互換性のため)

    Returns:
        repair_id 1行の集約 DataFrame
    """
    if "repair_id" not in df.columns:
        raise ValueError("df に repair_id カラムがありません")

    grouped = df.groupby("repair_id", sort=True, dropna=False)

    rows = []
    for repair_id, g in grouped:
        rows.append({
            "repair_id": repair_id,
            "product_type": _first_non_null(g.get("product_type")),
            "total_sub_records": len(g),
            "user_failure_codes": _join_codes(g.get("user_failure_code")),
            "repair_failure_codes": _join_codes(g.get("repair_failure_code")),
            "any_perspective_mismatch": _any_false(g.get("perspective_match")),
            "any_misjudged": _any_true(g.get("is_misjudged")),
            "any_harsh_env": _any_true(g.get("has_harsh_env")),
            "any_manufacturer_responsibility_repair": _any_true(
                g.get("is_manufacturer_responsibility_repair")
            ),
            "all_service_records": _all_true(g.get("is_service_record")),
            "min_confidence_overall": _min_value(g.get("min_confidence")),
        })

    return pd.DataFrame(rows)


def _first_non_null(s: pd.Series | None) -> Any:
    """Series の最初の非Null値を返す。"""
    if s is None or s.empty:
        return None
    non_null = s.dropna()
    return non_null.iloc[0] if len(non_null) > 0 else None


def _join_codes(s: pd.Series | None) -> str:
    """Series をユニーク化してカンマ区切り文字列にする (順序保持)。"""
    if s is None or s.empty:
        return ""
    seen: set[str] = set()
    out: list[str] = []
    for v in s:
        if isinstance(v, str) and v and v not in seen:
            seen.add(v)
            out.append(v)
    return ",".join(out)


def _any_true(s: pd.Series | None) -> bool:
    """Series 中に True が含まれるか。None/欠損は False 扱い。"""
    if s is None or s.empty:
        return False
    return bool(s.fillna(False).astype(bool).any())


def _any_false(s: pd.Series | None) -> bool:
    """Series 中に False が含まれるか (perspective_match=False=不一致あり、の判定用)。"""
    if s is None or s.empty:
        return False
    return bool((s.fillna(True).astype(bool) == False).any())  # noqa: E712


def _all_true(s: pd.Series | None) -> bool:
    """Series 中の値がすべて True か。空や全 NaN は False。"""
    if s is None or s.empty:
        return False
    cleaned = s.dropna()
    if cleaned.empty:
        return False
    return bool(cleaned.astype(bool).all())


def _min_value(s: pd.Series | None) -> float:
    """Series の最小値 (NaN 除外)。全 NaN/空なら NaN。"""
    if s is None or s.empty:
        return float("nan")
    cleaned = s.dropna()
    if cleaned.empty:
        return float("nan")
    return float(cleaned.min())


# =============================================================================
# CSV 書き出し
# =============================================================================

def write_csv(
    df: pd.DataFrame,
    path: Path,
    *,
    stringify_collections: bool = True,
) -> None:
    """
    DataFrame を UTF-8 (BOM 付き) で CSV に書き出す。

    Tableau Desktop は UTF-8 BOM を期待するため、`utf-8-sig` を使う。
    dict や list 型のカラムはそのままだと str(dict) 表記になり Tableau で扱いづらい
    ので、 JSON 文字列に変換する (stringify_collections=True のとき)。

    Args:
        df: 出力する DataFrame
        path: 出力先パス
        stringify_collections: True なら dict/list カラムを JSON 文字列化
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if stringify_collections:
        df = df.copy()
        for col in df.columns:
            if df[col].apply(lambda v: isinstance(v, (dict, list))).any():
                df[col] = df[col].apply(_to_json_str)

    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("CSV 書き出し完了: %s (rows=%d, cols=%d)", path, len(df), len(df.columns))


def _to_json_str(v: Any) -> Any:
    """dict/list を JSON 文字列に、それ以外はそのまま。"""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


# =============================================================================
# パイプライン (オールインワン)
# =============================================================================

def write_all_formats(
    df: pd.DataFrame,
    output_dir: Path | str,
    timestamp: str,
    codebook: CodeBook | None = None,
) -> dict[str, Path]:
    """
    3形式すべてを CSV 出力 + メタ JSON を出力する。

    Args:
        df: 派生指標計算済みの DataFrame (build_derived_dataframe の出力)
        output_dir: 出力ディレクトリ (例: outputs/final)
        timestamp: ファイル名プレフィックス (例: "20260513_120000")
        codebook: 集約形式で利用 (現状未使用、将来用)

    Returns:
        {"wide": Path, "long": Path, "aggregated": Path, "meta": Path} の dict
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    wide_df = to_wide_format(df)
    long_df = to_long_format(df)
    agg_df = to_aggregated_format(df, codebook=codebook)

    wide_path = output_dir / f"{timestamp}_wide.csv"
    long_path = output_dir / f"{timestamp}_long.csv"
    agg_path = output_dir / f"{timestamp}_aggregated.csv"
    meta_path = output_dir / f"{timestamp}_output_meta.json"

    write_csv(wide_df, wide_path)
    write_csv(long_df, long_path)
    write_csv(agg_df, agg_path)

    meta = _build_meta(df, wide_df, long_df, agg_df, timestamp)
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("メタ情報書き出し完了: %s", meta_path)

    return {
        "wide": wide_path,
        "long": long_path,
        "aggregated": agg_path,
        "meta": meta_path,
    }


def _build_meta(
    df: pd.DataFrame,
    wide_df: pd.DataFrame,
    long_df: pd.DataFrame,
    agg_df: pd.DataFrame,
    timestamp: str,
) -> dict:
    """出力メタ情報を組み立てる。"""
    meta: dict[str, Any] = {
        "timestamp": timestamp,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "input_rows": int(len(df)),
            "wide_rows": int(len(wide_df)),
            "long_rows": int(len(long_df)),
            "aggregated_rows": int(len(agg_df)),
            "unique_repair_ids": int(df["repair_id"].nunique()) if "repair_id" in df.columns else 0,
        },
    }
    # 派生指標サマリ
    summary: dict[str, Any] = {}
    for col in (
        "perspective_match",
        "is_misjudged",
        "has_harsh_env",
        "has_repair_confirmed_env",
        "is_manufacturer_responsibility_user",
        "is_manufacturer_responsibility_repair",
        "is_service_record",
    ):
        if col in df.columns:
            true_count = int(df[col].fillna(False).astype(bool).sum())
            summary[col] = {
                "true": true_count,
                "rate": round(true_count / len(df), 4) if len(df) else 0.0,
            }
    if "min_confidence" in df.columns:
        cleaned = df["min_confidence"].dropna()
        if not cleaned.empty:
            summary["min_confidence_stats"] = {
                "min": float(cleaned.min()),
                "mean": round(float(cleaned.mean()), 4),
                "median": float(cleaned.median()),
                "below_0.7": int((cleaned < 0.7).sum()),
            }
    meta["derived_metrics_summary"] = summary

    # 製品種別ごとの件数
    if "product_type" in df.columns:
        meta["product_type_counts"] = {
            str(k): int(v) for k, v in df["product_type"].value_counts(dropna=False).items()
        }

    return meta
