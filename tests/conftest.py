"""共通テスト設定。"""

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def yaml_path() -> Path:
    """classification_codes.yaml の実パス。"""
    return REPO_ROOT / "config" / "classification_codes.yaml"


@pytest.fixture(scope="session")
def codebook(yaml_path):
    """実 yaml をロードした CodeBook。"""
    from codes_loader import load_codes
    return load_codes(yaml_path)
