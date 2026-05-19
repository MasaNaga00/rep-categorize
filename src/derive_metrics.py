"""
derive_metrics.py
=================
records.json (チャット1出力) と classifications.json (チャット2出力) を
(repair_id, sub_id) で結合し、派生指標を計算する。

責務:
    - 2つの JSON を結合 (内部結合、件数差は warning)
    - perspective_match, is_misjudged, has_harsh_env 等の派生指標を計算
    - 環境要因の has_water, has_impact 等のフラグ展開
    - user_failure_code → user_failure_name 等のコード名付与
    - confidence 系の集約 (min_confidence)

設計判断:
    - environment_factors はリスト、environment_evidence_source 等は dict のまま保持
      (フラグ展開・ロング展開は output_formatter 側で必要に応じて使う)
    - codes_loader.is_manufacturer_responsibility() の仕様に従い、
      responsibility 未付与コードは「責任不明=False」として扱う (00_common.md 既定 α)
    - エラー耐性: 体系外コードや欠損が来ても落ちず、 該当指標は NaN/False で埋める

Usage:
    from codes_loader import load_codes
    from derive_metrics import build_derived_dataframe

    codebook = load_codes("config/classification_codes.yaml")
    df = build_derived_dataframe(records, classifications, codebook)
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from codes_loader import CodeBook

logger = logging.getLogger(__name__)


# =============================================================================
# 定数
# =============================================================================

# 環境要因のうち「厳しい環境」とみなさない値
_NON_HARSH_ENV = frozenset({"none", "unknown"})

# environment_evidence_source のうち「修理者確認あり」とみなす値
_REPAIR_CONFIRMED_SOURCES = frozenset({"repair", "both"})

# has_xxx フラグを展開する環境要因 (none/unknown は除外)
_ENV_FLAG_KEYS = ("water", "sand_dust", "impact", "heat", "cold", "humidity")


# =============================================================================
# 結合
# =============================================================================

def merge_records_and_classifications(
    records: list[dict],
    classifications: list[dict],
    how: str = "inner",
) -> pd.DataFrame:
    """
    records と classifications を (repair_id, sub_id) で結合する。

    Args:
        records: チャット1の出力 (records.json をデシリアライズしたもの)
        classifications: チャット2の出力 (classifications.json をデシリアライズしたもの)
        how: pandas merge の how パラメータ (デフォルト "inner")。
             "left" にすると失敗バッチのレコードを残せる (分類カラムが NaN)。

    Returns:
        結合済み DataFrame。

        ネスト構造の扱い:
            - user_perspective / repair_perspective は固定スキーマなので
              user_failure_code, user_confidence 等のフラットカラムに展開する
            - environment_evidence_source / environment_evidence /
              environment_confidence は dict のキーが動的 (water, impact, ...)
              なので、dict のまま保持する (展開すると列名が不安定)

    Raises:
        ValueError: records または classifications が空、または必須キー欠落、
                    または inner join 結果が 0 件
    """
    if not records:
        raise ValueError("records が空です")
    if not classifications:
        raise ValueError("classifications が空です")

    records_df = pd.DataFrame(records)
    _require_key_columns(records_df, "records")

    # 手動で perspective だけ平坦化、他は dict のまま保持
    class_df = pd.DataFrame([_flatten_classification(c) for c in classifications])
    _require_key_columns(class_df, "classifications")

    n_rec = len(records_df)
    n_cls = len(class_df)
    if n_rec != n_cls:
        logger.warning(
            "records と classifications の件数が異なります: "
            "records=%d, classifications=%d (差分=%d)",
            n_rec, n_cls, abs(n_rec - n_cls),
        )

    merged = records_df.merge(
        class_df,
        on=["repair_id", "sub_id"],
        how=how,
    )

    if how == "inner" and len(merged) == 0:
        raise ValueError(
            "結合結果が 0 件です。(repair_id, sub_id) の対応関係を確認してください。"
        )

    return merged


def _require_key_columns(df: pd.DataFrame, name: str) -> None:
    """主キー (repair_id, sub_id) の存在チェック。"""
    missing = {"repair_id", "sub_id"} - set(df.columns)
    if missing:
        raise ValueError(f"{name} に必須カラム {missing} がありません")


def _flatten_classification(c: dict) -> dict:
    """
    classifications の1レコードを部分的に平坦化する。

    - user_perspective.* / repair_perspective.* → user_xxx / repair_xxx に展開
    - environment_xxx の dict 値は dict のまま保持
    - その他のトップレベルキーはそのまま
    """
    out: dict = {}
    for key, value in c.items():
        if key == "user_perspective" and isinstance(value, dict):
            out["user_failure_code"] = value.get("failure_category_code")
            out["user_confidence"] = value.get("confidence")
            out["user_evidence"] = value.get("evidence")
            out["user_insufficient_info"] = value.get("insufficient_info")
        elif key == "repair_perspective" and isinstance(value, dict):
            out["repair_failure_code"] = value.get("failure_category_code")
            out["repair_confidence"] = value.get("confidence")
            out["repair_evidence"] = value.get("evidence")
            out["repair_insufficient_info"] = value.get("insufficient_info")
        else:
            # repair_id, sub_id, reproduction_*, environment_* (dict のまま) 等
            out[key] = value
    return out


# =============================================================================
# 派生指標計算
# =============================================================================

def calculate_derived_metrics(
    df: pd.DataFrame,
    codebook: CodeBook,
) -> pd.DataFrame:
    """
    派生指標カラムを追加した DataFrame を返す。

    追加されるカラム:
        - perspective_match: bool, ユーザ視点と修理者視点のコード一致
        - is_manufacturer_responsibility_user: bool
        - is_manufacturer_responsibility_repair: bool
        - is_misjudged: bool, ユーザと修理者で responsibility が異なる
        - has_harsh_env: bool, 環境要因が none/unknown 以外を含む
        - has_repair_confirmed_env: bool, 修理者が環境要因を確認している
        - is_service_record: bool, 修理者視点コードがサービスカテゴリ
        - min_confidence: float, 4タスクの confidence 最小値

    Args:
        df: merge_records_and_classifications の出力 DataFrame
        codebook: classification_codes.yaml をロードした CodeBook

    Returns:
        派生指標カラムが追加された DataFrame (元の df は変更しない)
    """
    df = df.copy()

    df["is_manufacturer_responsibility_user"] = df.apply(
        lambda r: _is_manufacturer(r.get("user_failure_code"), r.get("product_type"), codebook),
        axis=1,
    )
    df["is_manufacturer_responsibility_repair"] = df.apply(
        lambda r: _is_manufacturer(r.get("repair_failure_code"), r.get("product_type"), codebook),
        axis=1,
    )

    df["perspective_match"] = (
        df["user_failure_code"] == df["repair_failure_code"]
    )

    df["is_misjudged"] = (
        df["is_manufacturer_responsibility_user"]
        != df["is_manufacturer_responsibility_repair"]
    )

    df["is_service_record"] = df.apply(
        lambda r: _is_service(r.get("repair_failure_code"), r.get("product_type"), codebook),
        axis=1,
    )

    df["has_harsh_env"] = df["environment_factors"].apply(_has_harsh_env)

    df["has_repair_confirmed_env"] = df["environment_evidence_source"].apply(
        _has_repair_confirmed
    )

    df["min_confidence"] = df.apply(_compute_min_confidence, axis=1)

    return df


def _is_manufacturer(code: Any, product_type: Any, codebook: CodeBook) -> bool:
    """codebook.is_manufacturer_responsibility のラッパー (Null安全)。"""
    if not isinstance(code, str) or not isinstance(product_type, str):
        return False
    try:
        return codebook.is_manufacturer_responsibility(code, product_type)
    except ValueError:
        return False


def _is_service(code: Any, product_type: Any, codebook: CodeBook) -> bool:
    """codebook.is_service_record のラッパー (Null安全)。"""
    if not isinstance(code, str) or not isinstance(product_type, str):
        return False
    try:
        return codebook.is_service_record(code, product_type)
    except ValueError:
        return False


def _has_harsh_env(factors: Any) -> bool:
    """environment_factors リストに none/unknown 以外が含まれるか。"""
    if not isinstance(factors, list):
        return False
    return any(f not in _NON_HARSH_ENV for f in factors if isinstance(f, str))


def _has_repair_confirmed(env_evidence_source: Any) -> bool:
    """environment_evidence_source dict に "repair" or "both" が含まれるか。"""
    if not isinstance(env_evidence_source, dict):
        return False
    return any(
        src in _REPAIR_CONFIRMED_SOURCES
        for src in env_evidence_source.values()
        if isinstance(src, str)
    )


def _compute_min_confidence(row: pd.Series) -> float:
    """4タスクの confidence 最小値を計算 (環境要因は dict のため別処理)。"""
    values: list[float] = []
    for col in ("user_confidence", "repair_confidence", "reproduction_confidence"):
        v = row.get(col)
        if isinstance(v, (int, float)) and not pd.isna(v):
            values.append(float(v))

    env_conf = row.get("environment_confidence")
    if isinstance(env_conf, dict) and env_conf:
        env_values = [
            float(v) for v in env_conf.values()
            if isinstance(v, (int, float)) and not pd.isna(v)
        ]
        if env_values:
            values.append(min(env_values))

    if not values:
        return float("nan")
    return min(values)


# =============================================================================
# 環境要因フラグ展開
# =============================================================================

def expand_environment_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    environment_factors リストを has_water, has_impact 等の bool カラムに展開する。

    展開されるカラム (none/unknown は除外):
        - has_water, has_sand_dust, has_impact, has_heat, has_cold, has_humidity

    また、environment_factors を表示用にカンマ区切り文字列化した
    environment_factors_str カラムも追加する (元のリストは保持)。

    Args:
        df: calculate_derived_metrics 後の DataFrame (environment_factors を持つ)

    Returns:
        フラグカラムが追加された DataFrame
    """
    df = df.copy()

    for key in _ENV_FLAG_KEYS:
        col = f"has_{key}"
        df[col] = df["environment_factors"].apply(
            lambda factors, k=key: _has_env(factors, k)
        )

    df["environment_factors_str"] = df["environment_factors"].apply(_env_list_to_str)

    return df


def _has_env(factors: Any, key: str) -> bool:
    """environment_factors リストに key が含まれるか。"""
    if not isinstance(factors, list):
        return False
    return key in factors


def _env_list_to_str(factors: Any) -> str:
    """環境要因リストをカンマ区切り文字列化。"""
    if not isinstance(factors, list):
        return ""
    return ",".join(str(f) for f in factors)


# =============================================================================
# コード名付与
# =============================================================================

def add_failure_names(df: pd.DataFrame, codebook: CodeBook) -> pd.DataFrame:
    """
    user_failure_code → user_failure_name, repair_failure_code → repair_failure_name
    を追加する。

    体系外コードや欠損は空文字列にする。

    Args:
        df: user_failure_code, repair_failure_code, product_type を持つ DataFrame
        codebook: CodeBook

    Returns:
        user_failure_name, repair_failure_name が追加された DataFrame
    """
    df = df.copy()

    df["user_failure_name"] = df.apply(
        lambda r: _get_name(r.get("user_failure_code"), r.get("product_type"), codebook),
        axis=1,
    )
    df["repair_failure_name"] = df.apply(
        lambda r: _get_name(r.get("repair_failure_code"), r.get("product_type"), codebook),
        axis=1,
    )

    return df


def _get_name(code: Any, product_type: Any, codebook: CodeBook) -> str:
    """codebook.get_failure_category().name を取得 (Null安全)。"""
    if not isinstance(code, str) or not isinstance(product_type, str):
        return ""
    try:
        fc = codebook.get_failure_category(code, product_type)
    except ValueError:
        return ""
    if fc is None:
        return ""
    return fc.name


# =============================================================================
# パイプライン (オールインワン)
# =============================================================================

def build_derived_dataframe(
    records: list[dict],
    classifications: list[dict],
    codebook: CodeBook,
    how: str = "inner",
) -> pd.DataFrame:
    """
    結合 → 派生指標計算 → 環境フラグ展開 → コード名付与 を一括実行する。

    notebook 等で簡潔に書きたい時用のショートカット。

    Args:
        records: チャット1の出力
        classifications: チャット2の出力
        codebook: CodeBook
        how: merge の how

    Returns:
        全派生指標が追加された DataFrame
    """
    df = merge_records_and_classifications(records, classifications, how=how)
    df = calculate_derived_metrics(df, codebook)
    df = expand_environment_flags(df)
    df = add_failure_names(df, codebook)
    return df
