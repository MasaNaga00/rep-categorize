"""
test_prompt_builder.py
======================
prompt_builder.py（v2: Dify貼り付け用ツール）の単体テスト。

カバー範囲:
    - PromptBuilder の初期化
    - build_system_prompt（ML/LENS、特殊コード、JSON前提の文言）
    - build_user_message_template（Dify変数プレースホルダ）
    - build_user_message_for_records（実レコード埋め込み、JSON出力）
    - レコードバリデーション、キー順序固定
    - バッチ分割ヘルパー
    - CLI 機能
    - split.py 出力との統合
"""

from __future__ import annotations

import json, sys
from pathlib import Path

import pytest

# srcをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from codes_loader import ProductType, load_codes
from prompt_builder import (
    DIFY_RECORDS_VARIABLE,
    DIFY_RECORDS_COUNT_VARIABLE,
    PromptBuilder,
    PromptBuilderError,
    RECORD_KEY_ORDER,
    _generate_all_prompts,
    main as cli_main,
)


# =============================================================================
# fixtures
# =============================================================================

REAL_YAML_PATH = Path(__file__).parent.parent / "config" / "classification_codes.yaml"
TEMPLATE_DIR = Path(__file__).parent.parent / "config" / "prompts"


@pytest.fixture(scope="module")
def codebook():
    return load_codes(REAL_YAML_PATH)


@pytest.fixture(scope="module")
def builder(codebook):
    return PromptBuilder(codebook=codebook, template_dir=TEMPLATE_DIR)


@pytest.fixture
def sample_record_full():
    return {
        "repair_id": "R001",
        "sub_id": 1,
        "user_text": "AFが効きません",
        "user_context": "H111",
        "repair_text": "AFユニット交換",
        "repair_context": "保証期間内",
        "internal_1": "顧客優先",
        "internal_2": "",
    }


@pytest.fixture
def sample_record_minimal():
    return {
        "repair_id": "R002",
        "sub_id": 1,
        "user_text": "",
        "user_context": "",
        "repair_text": "",
        "repair_context": "",
        "internal_1": "",
        "internal_2": "",
    }


# =============================================================================
# 初期化
# =============================================================================

class TestPromptBuilderInit:
    def test_init_with_real_codebook(self, codebook):
        builder = PromptBuilder(codebook=codebook, template_dir=TEMPLATE_DIR)
        assert builder.codebook is codebook

    def test_init_invalid_template_dir(self, codebook):
        with pytest.raises(PromptBuilderError, match="テンプレートディレクトリ"):
            PromptBuilder(codebook=codebook, template_dir="/nonexistent")


# =============================================================================
# システムプロンプト
# =============================================================================

class TestBuildSystemPrompt:
    def test_ml_prompt_contains_basic_info(self, builder):
        prompt = builder.build_system_prompt("ML")
        assert "ミラーレスカメラ" in prompt
        assert "（ML）" in prompt
        assert "M_UNK" in prompt
        assert "M046" in prompt

    def test_lens_prompt_contains_basic_info(self, builder):
        prompt = builder.build_system_prompt("LENS")
        assert "交換レンズ" in prompt
        assert "（LENS）" in prompt
        assert "L_UNK" in prompt
        assert "L036" in prompt

    def test_ml_includes_failure_codes(self, builder):
        prompt = builder.build_system_prompt("ML")
        assert "M001" in prompt
        assert "M005" in prompt
        assert "M012" in prompt
        assert "M013" in prompt

    def test_ml_excludes_lens_codes(self, builder):
        prompt = builder.build_system_prompt("ML")
        assert "L001" not in prompt
        assert "L036" not in prompt

    def test_lens_excludes_ml_codes(self, builder):
        prompt = builder.build_system_prompt("LENS")
        assert "M001" not in prompt
        assert "M046" not in prompt

    def test_decision_rule_included(self, builder):
        prompt = builder.build_system_prompt("ML")
        assert "迷ったら" in prompt or "外側" in prompt

    def test_responsibility_NOT_included(self, builder):
        """設計判断: responsibility はプロンプトに含めない。"""
        prompt = builder.build_system_prompt("ML")
        assert "responsibility" not in prompt
        assert "manufacturer" not in prompt
        assert "user_or_unknown" not in prompt

    def test_environment_factors_with_keywords(self, builder):
        prompt = builder.build_system_prompt("ML")
        assert "water" in prompt
        assert "sand_dust" in prompt
        assert "水没" in prompt or "海" in prompt
        assert "落下" in prompt or "衝撃" in prompt

    def test_reproduction_statuses_in_order(self, builder):
        prompt = builder.build_system_prompt("ML")
        for key in ["reproduced", "not_reproduced", "partial", "not_attempted"]:
            assert key in prompt
        assert prompt.index("reproduced") < prompt.index("not_attempted")

    def test_input_data_spec_mentions_json(self, builder):
        """JSON前提の入力仕様が記述されている。"""
        prompt = builder.build_system_prompt("ML")
        assert "JSON" in prompt
        assert "string" in prompt or "integer" in prompt  # 型指定がある

    def test_ml_with_enum(self, builder):
        prompt = builder.build_system_prompt(ProductType.ML)
        assert "ミラーレスカメラ" in prompt

    def test_unknown_product_type(self, builder):
        with pytest.raises(PromptBuilderError, match="未知の製品種別"):
            builder.build_system_prompt("UNKNOWN")


# =============================================================================
# Dify貼り付け用ユーザプロンプトテンプレート
# =============================================================================

class TestBuildUserMessageTemplate:
    def test_contains_dify_variables(self, builder):
        """Dify用の変数プレースホルダが含まれる。"""
        template = builder.build_user_message_template()
        assert "{{" + DIFY_RECORDS_VARIABLE + "}}" in template
        assert "{{" + DIFY_RECORDS_COUNT_VARIABLE + "}}" in template

    def test_template_has_json_block(self, builder):
        template = builder.build_user_message_template()
        assert "```json" in template

    def test_template_independent_of_product(self, builder):
        """テンプレートは製品種別に依存しない。"""
        template = builder.build_user_message_template()
        # ML/LENS の文字が含まれていないことを確認
        assert "ML" not in template
        assert "LENS" not in template


# =============================================================================
# 実レコード埋め込みのユーザメッセージ
# =============================================================================

class TestBuildUserMessageForRecords:
    def test_basic(self, builder, sample_record_full):
        msg = builder.build_user_message_for_records([sample_record_full])
        assert "1 件" in msg
        assert "R001" in msg

    def test_json_block_present(self, builder, sample_record_full):
        msg = builder.build_user_message_for_records([sample_record_full])
        assert "```json" in msg

    def test_japanese_not_escaped(self, builder, sample_record_full):
        """日本語が \\u エスケープされず生のまま出力される。"""
        msg = builder.build_user_message_for_records([sample_record_full])
        assert "AFが効きません" in msg
        assert "\\u" not in msg or "\\u" not in msg.replace("\\\\u", "")

    def test_record_keys_in_fixed_order(self, builder, sample_record_full):
        """JSONレコードのキー順序が固定。"""
        msg = builder.build_user_message_for_records([sample_record_full])
        # JSON部分を抽出
        json_start = msg.index("```json") + len("```json\n")
        json_end = msg.index("```", json_start)
        json_str = msg[json_start:json_end]
        records = json.loads(json_str)

        # 最初のレコードのキー順を確認
        actual_keys = list(records[0].keys())
        assert actual_keys == RECORD_KEY_ORDER

    def test_multiple_records(self, builder, sample_record_full, sample_record_minimal):
        msg = builder.build_user_message_for_records(
            [sample_record_full, sample_record_minimal]
        )
        assert "2 件" in msg
        assert "R001" in msg
        assert "R002" in msg

    def test_empty_records_raises(self, builder):
        with pytest.raises(PromptBuilderError, match="レコードが空"):
            builder.build_user_message_for_records([])

    def test_missing_required_keys(self, builder):
        bad_record = {"repair_id": "X", "sub_id": 1}
        with pytest.raises(PromptBuilderError, match="必須キーが不足"):
            builder.build_user_message_for_records([bad_record])

    def test_extra_keys_filtered_out(self, builder):
        """余計なキー（product_type 等）はJSONに含まれない。"""
        record = {
            "repair_id": "R001", "sub_id": 1,
            "user_text": "test", "user_context": "",
            "repair_text": "test", "repair_context": "",
            "internal_1": "", "internal_2": "",
            "product_type": "ML",  # 余計なキー
            "split_info": "no_markers",  # split.py 由来の余計なキー
        }
        msg = builder.build_user_message_for_records([record])
        assert "product_type" not in msg
        assert "split_info" not in msg

    def test_json_is_valid(self, builder, sample_record_full):
        """生成された JSON が valid な JSON である。"""
        msg = builder.build_user_message_for_records([sample_record_full])
        json_start = msg.index("```json") + len("```json\n")
        json_end = msg.index("```", json_start)
        json_str = msg[json_start:json_end]
        # パース可能であること
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)
        assert len(parsed) == 1


# =============================================================================
# バッチ分割ヘルパー
# =============================================================================

class TestSplitIntoBatches:
    def test_exact_division(self):
        records = [{"i": i} for i in range(20)]
        batches = PromptBuilder.split_into_batches(records, batch_size=10)
        assert len(batches) == 2
        assert all(len(b) == 10 for b in batches)

    def test_uneven_division(self):
        records = [{"i": i} for i in range(25)]
        batches = PromptBuilder.split_into_batches(records, batch_size=10)
        assert len(batches) == 3
        assert len(batches[2]) == 5

    def test_smaller_than_batch(self):
        records = [{"i": i} for i in range(3)]
        batches = PromptBuilder.split_into_batches(records, batch_size=10)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_empty_records(self):
        batches = PromptBuilder.split_into_batches([], batch_size=10)
        assert batches == []

    def test_invalid_batch_size(self):
        with pytest.raises(ValueError):
            PromptBuilder.split_into_batches([{"i": 1}], batch_size=0)


# =============================================================================
# CLI 機能
# =============================================================================

class TestCli:
    def test_generate_all_prompts(self, codebook, tmp_path):
        """_generate_all_prompts が全成果物を出力。"""
        generated = _generate_all_prompts(
            codebook=codebook,
            template_dir=TEMPLATE_DIR,
            output_dir=tmp_path,
        )
        assert "system_ML" in generated
        assert "system_LENS" in generated
        assert "user_template" in generated
        assert "readme" in generated

        for name, path in generated.items():
            assert path.exists(), f"{name}: {path} not found"
            assert path.stat().st_size > 0, f"{name}: empty file"

    def test_cli_main_default(self, tmp_path, monkeypatch):
        """CLI のデフォルト実行。"""
        # 作業ディレクトリをプロジェクトルートに
        monkeypatch.chdir(Path(__file__).parent.parent)
        output_dir = tmp_path / "out"
        result = cli_main(["--output", str(output_dir)])
        assert result == 0
        assert (output_dir / "system_prompt_ML.txt").exists()
        assert (output_dir / "system_prompt_LENS.txt").exists()
        assert (output_dir / "user_prompt_template.txt").exists()
        assert (output_dir / "README.md").exists()

    def test_cli_main_specific_product(self, tmp_path, monkeypatch):
        """製品種別を指定すると ML のみ生成。"""
        monkeypatch.chdir(Path(__file__).parent.parent)
        output_dir = tmp_path / "out"
        result = cli_main([
            "--product", "ML",
            "--output", str(output_dir),
        ])
        assert result == 0
        assert (output_dir / "system_prompt_ML.txt").exists()
        # LENS は生成されないが user_template と readme は生成される
        assert not (output_dir / "system_prompt_LENS.txt").exists()
        assert (output_dir / "user_prompt_template.txt").exists()


# =============================================================================
# 統合: split.py の出力をそのまま渡す
# =============================================================================

class TestIntegrationWithSplit:
    def test_split_output_works_directly(self, builder):
        import pandas as pd
        from split import split_records

        df = pd.DataFrame([{
            "repair_id": "R001", "product_type": "ML",
            "user_comment": "①AF不良 ②電源不良",
            "repair_comment": "①AF調整 ②電池清掃",
            "internal_1": "", "internal_2": "",
        }])

        split_df, _ = split_records(df)
        records = split_df.to_dict("records")

        msg = builder.build_user_message_for_records(records)
        assert "AF不良" in msg
        assert "電源不良" in msg
        # split_info 等の余計なキーは含まれない
        assert "split_info" not in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
