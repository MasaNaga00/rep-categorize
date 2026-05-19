"""
codes_loader.py
==================
classification_codes.yaml のロード・バリデーション。

責務:
    - YAMLを読み込んで pydantic モデルに変換
    - 内容整合性のバリデーション（レベル2）
    - 他モジュールから使う代表的なヘルパー関数の提供

Usage:
    from codes_loader import load_codes

    codebook = load_codes("config/classification_codes.yaml")
    codes = codebook.get_failure_codes_for_product("ML")
    is_manufacturer = codebook.is_manufacturer_responsibility("M012", "ML")
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


# =============================================================================
# Enum定義（YAML値と1:1対応）
# =============================================================================

class ProductType(str, Enum):
    """製品種別。SQL段階で判定される。"""
    ML = "ML"
    LENS = "LENS"


class RecordType(str, Enum):
    """レコード種別。故障/サービス/不明を区別。"""
    FAILURE = "failure"
    SERVICE = "service"
    UNKNOWN = "unknown"


class Responsibility(str, Enum):
    """メーカー責任判定。センサー内/外などで使用。"""
    MANUFACTURER = "manufacturer"
    USER_OR_UNKNOWN = "user_or_unknown"


class DescriptionStatus(str, Enum):
    """description のレビュー状態。"""
    AI_INFERRED = "ai_inferred"
    VERIFIED = "verified"
    DRAFT = "draft"


class EnvSource(str, Enum):
    """環境要因の判定ソース。"""
    USER = "user"
    REPAIR = "repair"
    BOTH = "both"


# =============================================================================
# 例外定義
# =============================================================================

class CodeBookLoadError(Exception):
    """YAMLロード・バリデーション失敗時の例外。"""
    pass


# =============================================================================
# pydanticモデル定義
# =============================================================================

class FailureCategory(BaseModel):
    """1つの故障分類コード。"""
    model_config = ConfigDict(extra="allow")  # 将来の属性追加に寛容

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    record_type: RecordType
    description_status: DescriptionStatus = DescriptionStatus.AI_INFERRED
    responsibility: Responsibility | None = None
    is_special: bool = False
    decision_rule: str | None = None


class EnvironmentFactor(BaseModel):
    """環境要因の1要素。"""
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    description_status: DescriptionStatus = DescriptionStatus.VERIFIED
    keywords: list[str] = Field(default_factory=list)
    is_special: bool = False


class ReproductionStatus(BaseModel):
    """再現状況の1要素。"""
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    description_status: DescriptionStatus = DescriptionStatus.VERIFIED
    keywords: list[str] = Field(default_factory=list)


class ProductCategory(BaseModel):
    """製品種別ごとの故障分類セット。"""
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    failure_categories: dict[str, FailureCategory] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_required_special_codes(self) -> "ProductCategory":
        """OTHER と UNK が存在することを確認（レベル2: 内容整合性）。"""
        # 特殊コードが少なくとも1つ「その他」相当、1つ「判定不能」相当存在することを確認
        special_codes = {
            code: fc for code, fc in self.failure_categories.items()
            if fc.is_special
        }
        if not special_codes:
            raise ValueError(
                f"製品 '{self.name}' に特殊コード（OTHER/UNK）が定義されていません。"
                "is_special=true のコードを最低1つ用意してください。"
            )

        # UNK 系コード（record_type=unknown）の存在確認
        unk_codes = [
            code for code, fc in self.failure_categories.items()
            if fc.record_type == RecordType.UNKNOWN
        ]
        if not unk_codes:
            raise ValueError(
                f"製品 '{self.name}' に判定不能コード（record_type=unknown）が"
                "定義されていません。"
            )
        return self


class ChangelogEntry(BaseModel):
    """変更履歴の1エントリ。"""
    model_config = ConfigDict(extra="allow")

    version: str
    date: str
    changes: list[str]


class CodeBookMeta(BaseModel):
    """YAML全体のメタ情報。"""
    model_config = ConfigDict(extra="allow")

    version: str = Field(..., min_length=1)
    source: str | None = None
    created_at: str | None = None
    total_codes: dict[str, int] | None = None
    description_status: DescriptionStatus | None = None
    notes: str | None = None
    changelog: list[ChangelogEntry] = Field(default_factory=list)


class DualPerspectiveRules(BaseModel):
    """2軸分類ルールの構造。"""
    model_config = ConfigDict(extra="allow")

    overview: str
    user_perspective: dict[str, Any]
    repair_perspective: dict[str, Any]


class ClassificationRules(BaseModel):
    """分類ルール集。"""
    model_config = ConfigDict(extra="allow")

    general: list[str] = Field(default_factory=list)
    dual_perspective: DualPerspectiveRules | None = None
    reproduction_handling: list[str] = Field(default_factory=list)
    service_records: list[str] = Field(default_factory=list)
    responsibility_aware: list[str] = Field(default_factory=list)


class CodeBook(BaseModel):
    """
    classification_codes.yaml 全体のルートモデル。

    他モジュールはこのオブジェクトを通じてコード体系にアクセスする。
    """
    model_config = ConfigDict(extra="allow")

    meta: CodeBookMeta
    product_categories: dict[str, ProductCategory] = Field(..., min_length=1)
    environment_factors: dict[str, EnvironmentFactor] = Field(..., min_length=1)
    reproduction_statuses: dict[str, ReproductionStatus] = Field(..., min_length=1)
    classification_rules: ClassificationRules | None = None

    @field_validator("product_categories")
    @classmethod
    def _check_known_product_types(
        cls, v: dict[str, ProductCategory]
    ) -> dict[str, ProductCategory]:
        """製品種別キーが ProductType Enum と一致することを確認。"""
        known = {p.value for p in ProductType}
        unknown = set(v.keys()) - known
        if unknown:
            raise ValueError(
                f"未知の製品種別が含まれています: {unknown}. "
                f"許可される値: {known}"
            )
        return v

    @model_validator(mode="after")
    def _check_required_environment_specials(self) -> "CodeBook":
        """環境要因に none と unknown が必須であることを確認。"""
        required = {"none", "unknown"}
        actual = set(self.environment_factors.keys())
        missing = required - actual
        if missing:
            raise ValueError(
                f"environment_factors に必須キーが欠けています: {missing}"
            )
        return self

    @model_validator(mode="after")
    def _check_required_reproduction_statuses(self) -> "CodeBook":
        """再現状況に4つの必須値が含まれることを確認。"""
        required = {"reproduced", "not_reproduced", "partial", "not_attempted"}
        actual = set(self.reproduction_statuses.keys())
        missing = required - actual
        if missing:
            raise ValueError(
                f"reproduction_statuses に必須キーが欠けています: {missing}"
            )
        return self

    # =========================================================================
    # ヘルパー関数（代表的なもの）
    # =========================================================================

    def get_failure_codes_for_product(
        self, product_type: str | ProductType
    ) -> dict[str, FailureCategory]:
        """
        指定製品種別の故障コード辞書を返す。

        Args:
            product_type: "ML" または "LENS"

        Returns:
            コード文字列 → FailureCategory の辞書
        """
        pt = product_type.value if isinstance(product_type, ProductType) else product_type
        if pt not in self.product_categories:
            raise ValueError(
                f"未知の製品種別: {pt}. "
                f"利用可能: {list(self.product_categories.keys())}"
            )
        return self.product_categories[pt].failure_categories

    def get_failure_category(
        self, code: str, product_type: str | ProductType
    ) -> FailureCategory | None:
        """
        指定故障コードの FailureCategory オブジェクトを返す。

        derive_metrics.py で name や record_type を取り出す用途を想定。
        コードが体系外なら None を返す（厳格にしない、is_valid_failure_code と
        組み合わせて使うことを想定）。

        Args:
            code: 故障コード（例: "M012"）
            product_type: "ML" または "LENS"

        Returns:
            FailureCategory オブジェクト、または存在しない場合は None
        """
        codes = self.get_failure_codes_for_product(product_type)
        return codes.get(code)

    def is_valid_failure_code(
        self, code: str, product_type: str | ProductType
    ) -> bool:
        """
        故障コードが指定製品種別の体系内に存在するかを検証。

        LLM出力の妥当性チェックに使う。

        Args:
            code: 検証する故障コード（例: "M012"）
            product_type: "ML" または "LENS"

        Returns:
            体系内に存在すれば True
        """
        codes = self.get_failure_codes_for_product(product_type)
        return code in codes

    def is_manufacturer_responsibility(
        self, code: str, product_type: str | ProductType
    ) -> bool:
        """
        指定故障コードがメーカー責任判定（responsibility=manufacturer）かを返す。

        derive_metrics.py で派生指標を計算するときに使う。
        コードが存在しない場合は False を返す（厳格にしない）。

        Args:
            code: 故障コード
            product_type: 製品種別

        Returns:
            responsibility=manufacturer なら True、それ以外（未定義含む）は False
        """
        codes = self.get_failure_codes_for_product(product_type)
        fc = codes.get(code)
        if fc is None:
            return False
        return fc.responsibility == Responsibility.MANUFACTURER

    def is_service_record(
        self, code: str, product_type: str | ProductType
    ) -> bool:
        """
        指定故障コードがサービスレコード（record_type=service）かを返す。

        derive_metrics.py で使う。

        Args:
            code: 故障コード
            product_type: 製品種別

        Returns:
            record_type=service なら True
        """
        codes = self.get_failure_codes_for_product(product_type)
        fc = codes.get(code)
        if fc is None:
            return False
        return fc.record_type == RecordType.SERVICE


# =============================================================================
# ロード関数（モジュールのエントリーポイント）
# =============================================================================

def load_codes(path: str | Path) -> CodeBook:
    """
    classification_codes.yaml をロード・バリデーションして CodeBook を返す。

    Args:
        path: YAMLファイルパス

    Returns:
        バリデーション済みの CodeBook オブジェクト

    Raises:
        CodeBookLoadError: ファイル不在、YAML構文エラー、バリデーション失敗のいずれか
    """
    path = Path(path)

    if not path.exists():
        raise CodeBookLoadError(f"YAMLファイルが見つかりません: {path}")

    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise CodeBookLoadError(f"YAML構文エラー: {path}\n{e}") from e

    if not isinstance(raw, dict):
        raise CodeBookLoadError(
            f"YAMLのルートが辞書ではありません: {type(raw).__name__}"
        )

    try:
        return CodeBook.model_validate(raw)
    except ValidationError as e:
        raise CodeBookLoadError(
            f"バリデーション失敗: {path}\n{e}"
        ) from e
