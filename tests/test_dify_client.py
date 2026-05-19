"""
test_dify_client.py
===================
dify_client.py の単体テスト。

requests_mock / aioresponses でDify APIをモック化し、
実APIキーなしで動作確認する。
"""

from __future__ import annotations

import json
import os

import aiohttp
import pytest
import requests_mock as rm_lib
from aioresponses import aioresponses

from dify_client import (
    BatchResult,
    DifyAPIError,
    DifyAuthError,
    DifyClient,
    DifyClientError,
    DifyResponseParseError,
    DifyTimeoutError,
    RunReport,
    _build_payload,
    _parse_dify_response,
    _parse_llm_classifications,
    _split_into_batches,
    collect_failed_records,
    flatten_results,
)


# =============================================================================
# 共通fixture
# =============================================================================

TEST_BASE_URL = "https://dify.test.example.com"
TEST_API_KEY = "app-test-key-12345"


@pytest.fixture
def client():
    """テスト用クライアント（タイムアウト・リトライを短く）"""
    return DifyClient(
        base_url=TEST_BASE_URL,
        api_key=TEST_API_KEY,
        timeout_seconds=10,
        max_retries=2,
    )


@pytest.fixture
def sample_records():
    """split.py 出力相当のレコード3件。"""
    return [
        {
            "repair_id": "R001", "sub_id": 1,
            "user_text": "AFが効きません", "user_context": "",
            "repair_text": "AFユニット交換", "repair_context": "",
            "internal_1": "", "internal_2": "",
        },
        {
            "repair_id": "R002", "sub_id": 1,
            "user_text": "電源入らず", "user_context": "",
            "repair_text": "電池接点清掃", "repair_context": "",
            "internal_1": "", "internal_2": "",
        },
        {
            "repair_id": "R003", "sub_id": 1,
            "user_text": "シャッター不良", "user_context": "",
            "repair_text": "シャッターブロック交換", "repair_context": "",
            "internal_1": "", "internal_2": "",
        },
    ]


def _make_dify_response(classifications: list[dict], status: str = "succeeded") -> dict:
    """Dify Workflow API の正常レスポンス形式を生成。"""
    return {
        "workflow_run_id": "test-run-id",
        "task_id": "test-task-id",
        "data": {
            "id": "test-id",
            "workflow_id": "test-workflow",
            "status": status,
            "outputs": {
                "result": json.dumps(classifications, ensure_ascii=False),
            },
            "error": None,
            "elapsed_time": 5.0,
            "total_tokens": 1500,
            "total_steps": 5,
        },
    }


def _make_classification(repair_id: str, sub_id: int = 1, code: str = "M005") -> dict:
    """LLM分類結果のサンプル。"""
    return {
        "repair_id": repair_id,
        "sub_id": sub_id,
        "user_perspective": {
            "failure_category_code": code,
            "confidence": 0.9,
            "evidence": "test",
            "insufficient_info": False,
        },
        "repair_perspective": {
            "failure_category_code": code,
            "confidence": 0.95,
            "evidence": "test",
            "insufficient_info": False,
        },
        "reproduction_status": "reproduced",
        "reproduction_evidence": "test",
        "reproduction_confidence": 0.9,
        "environment_factors": ["unknown"],
        "environment_evidence_source": {"unknown": "user"},
        "environment_evidence": {"unknown": "なし"},
        "environment_confidence": {"unknown": 1.0},
    }


# =============================================================================
# 初期化・認証
# =============================================================================

class TestInit:
    def test_explicit_args(self):
        c = DifyClient(base_url="https://x.example.com", api_key="app-foo")
        assert c.base_url == "https://x.example.com"
        assert c.api_key == "app-foo"

    def test_strips_trailing_slash(self):
        c = DifyClient(base_url="https://x.example.com/", api_key="app-foo")
        assert c.base_url == "https://x.example.com"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("DIFY_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("DIFY_API_KEY", "app-env-key")
        c = DifyClient()
        assert c.base_url == "https://env.example.com"
        assert c.api_key == "app-env-key"

    def test_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("DIFY_BASE_URL", "https://env.example.com")
        c = DifyClient(base_url="https://arg.example.com", api_key="app-x")
        assert c.base_url == "https://arg.example.com"

    def test_missing_base_url(self, monkeypatch):
        monkeypatch.delenv("DIFY_BASE_URL", raising=False)
        with pytest.raises(DifyClientError, match="base_url"):
            DifyClient(api_key="app-foo")

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("DIFY_API_KEY", raising=False)
        with pytest.raises(DifyClientError, match="api_key"):
            DifyClient(base_url="https://x.example.com")


# =============================================================================
# ペイロード構築
# =============================================================================

class TestBuildPayload:
    def test_basic(self, sample_records):
        payload = _build_payload(sample_records, "ML")
        assert payload["inputs"]["product_type"] == "ML"
        assert payload["inputs"]["n_records"] == 3
        assert payload["response_mode"] == "blocking"

        # records_json は文字列
        records_json = payload["inputs"]["records_json"]
        assert isinstance(records_json, str)
        # 日本語が \\u エスケープされず生のまま
        assert "AFが効きません" in records_json

    def test_empty_records_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _build_payload([], "ML")

    def test_invalid_product_type(self, sample_records):
        with pytest.raises(ValueError, match="product_type"):
            _build_payload(sample_records, "UNKNOWN")


# =============================================================================
# レスポンスパース
# =============================================================================

class TestParseDifyResponse:
    def test_basic(self):
        classifications = [_make_classification("R001")]
        resp = _make_dify_response(classifications)
        text, meta = _parse_dify_response(resp)
        assert isinstance(text, str)
        assert "R001" in text
        assert meta["total_tokens"] == 1500
        assert meta["elapsed_time"] == 5.0

    def test_failed_status(self):
        resp = _make_dify_response([], status="failed")
        with pytest.raises(DifyClientError, match="status=failed"):
            _parse_dify_response(resp)

    def test_missing_data(self):
        with pytest.raises(DifyAPIError, match="data"):
            _parse_dify_response({"foo": "bar"})


class TestParseLlmClassifications:
    def test_pure_json_array(self):
        cls = [_make_classification("R001")]
        text = json.dumps(cls)
        result = _parse_llm_classifications(text)
        assert len(result) == 1
        assert result[0]["repair_id"] == "R001"

    def test_strips_code_fence(self):
        cls = [_make_classification("R001")]
        text = "```json\n" + json.dumps(cls) + "\n```"
        result = _parse_llm_classifications(text)
        assert len(result) == 1

    def test_strips_plain_code_fence(self):
        cls = [_make_classification("R001")]
        text = "```\n" + json.dumps(cls) + "\n```"
        result = _parse_llm_classifications(text)
        assert len(result) == 1

    def test_wrapped_in_results_key(self):
        """JSON Mode で {'results': [...]} に包まれているケース。"""
        cls = [_make_classification("R001")]
        text = json.dumps({"results": cls})
        result = _parse_llm_classifications(text)
        assert len(result) == 1
        assert result[0]["repair_id"] == "R001"

    def test_invalid_json(self):
        with pytest.raises(DifyResponseParseError):
            _parse_llm_classifications("not json at all")

    def test_object_without_known_key(self):
        text = json.dumps({"foo": "bar"})
        with pytest.raises(DifyResponseParseError, match="配列でも"):
            _parse_llm_classifications(text)


# =============================================================================
# 同期 API: 1バッチ実行
# =============================================================================

class TestRunWorkflow:
    def test_success(self, client, sample_records):
        classifications = [
            _make_classification(r["repair_id"]) for r in sample_records
        ]
        with rm_lib.Mocker() as m:
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                json=_make_dify_response(classifications),
            )
            result, meta = client.run_workflow(sample_records, "ML")

        assert len(result) == 3
        assert result[0]["repair_id"] == "R001"
        assert meta["total_tokens"] == 1500

    def test_auth_error_no_retry(self, client, sample_records):
        """401は即座に失敗、リトライしない。"""
        with rm_lib.Mocker() as m:
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                status_code=401,
                text="Unauthorized",
            )
            with pytest.raises(DifyAuthError):
                client.run_workflow(sample_records, "ML")
            # 1回だけ呼ばれるはず（リトライなし）
            assert len(m.request_history) == 1

    def test_5xx_retries(self, client, sample_records):
        """5xx はリトライされる。"""
        classifications = [_make_classification("R001")]
        with rm_lib.Mocker() as m:
            # 最初は500、次は成功
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                [
                    {"status_code": 500, "text": "Internal Server Error"},
                    {"json": _make_dify_response(classifications)},
                ],
            )
            result, _ = client.run_workflow(sample_records[:1], "ML")
            assert len(result) == 1
            assert len(m.request_history) == 2

    def test_invalid_json_retries_then_fails(self, client, sample_records):
        """JSON壊れはリトライされる。最終的に失敗。"""
        bad_response = {
            "data": {
                "status": "succeeded",
                "outputs": {"result": "not json"},
                "elapsed_time": 1.0,
                "total_tokens": 100,
            },
        }
        with rm_lib.Mocker() as m:
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                json=bad_response,
            )
            with pytest.raises(DifyClientError):
                client.run_workflow(sample_records[:1], "ML")
            # max_retries=2 なので2回試行
            assert len(m.request_history) == 2


# =============================================================================
# 同期 API: バッチ実行
# =============================================================================

class TestRunBatches:
    def test_all_succeed(self, client, sample_records):
        # 3件を batch_size=2 で分割すると 2バッチ（2件+1件）
        cls_b1 = [_make_classification(r["repair_id"]) for r in sample_records[:2]]
        cls_b2 = [_make_classification(r["repair_id"]) for r in sample_records[2:]]

        with rm_lib.Mocker() as m:
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                [
                    {"json": _make_dify_response(cls_b1)},
                    {"json": _make_dify_response(cls_b2)},
                ],
            )
            results, report = client.run_batches(
                sample_records, "ML", batch_size=2,
            )

        assert len(results) == 2
        assert all(r.success for r in results)
        assert report.successful_batches == 2
        assert report.failed_batches == 0
        assert report.total_input_records == 3
        assert report.total_output_records == 3

    def test_partial_failure_continues(self, client, sample_records):
        """1バッチ目失敗、2バッチ目成功でも処理継続。"""
        cls_b2 = [_make_classification(r["repair_id"]) for r in sample_records[2:]]

        with rm_lib.Mocker() as m:
            # 1バッチ目: 500エラーが連続（max_retries=2 なので2回）
            # 2バッチ目: 成功
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                [
                    {"status_code": 500, "text": "fail"},
                    {"status_code": 500, "text": "fail"},
                    {"json": _make_dify_response(cls_b2)},
                ],
            )
            results, report = client.run_batches(
                sample_records, "ML", batch_size=2,
            )

        assert len(results) == 2
        assert results[0].success is False
        assert results[1].success is True
        assert report.successful_batches == 1
        assert report.failed_batches == 1
        assert report.failed_batch_indices == [0]

    def test_auth_error_stops_immediately(self, client, sample_records):
        """認証エラーは即座に処理停止。"""
        with rm_lib.Mocker() as m:
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                status_code=401, text="Unauthorized",
            )
            with pytest.raises(DifyAuthError):
                client.run_batches(sample_records, "ML", batch_size=2)


# =============================================================================
# 非同期 API
# =============================================================================

@pytest.mark.asyncio
class TestAsyncRunWorkflow:
    async def test_success(self, client, sample_records):
        classifications = [_make_classification("R001")]
        with aioresponses() as m:
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                payload=_make_dify_response(classifications),
            )
            result, meta = await client.run_workflow_async(
                sample_records[:1], "ML",
            )
        assert len(result) == 1
        assert meta["total_tokens"] == 1500

    async def test_auth_error_no_retry(self, client, sample_records):
        with aioresponses() as m:
            m.post(
                f"{TEST_BASE_URL}/v1/workflows/run",
                status=401, body="Unauthorized",
            )
            with pytest.raises(DifyAuthError):
                await client.run_workflow_async(sample_records[:1], "ML")


@pytest.mark.asyncio
class TestAsyncRunBatches:
    async def test_parallel_execution(self, client, sample_records):
        """3バッチ並列実行で全成功。"""
        cls_template = [_make_classification("R001")]
        with aioresponses() as m:
            # 3バッチとも成功レスポンス
            for _ in range(3):
                m.post(
                    f"{TEST_BASE_URL}/v1/workflows/run",
                    payload=_make_dify_response(cls_template),
                )
            results, report = await client.run_batches_async(
                sample_records, "ML", batch_size=1, max_concurrent=3,
            )

        assert len(results) == 3
        assert all(r.success for r in results)
        assert report.successful_batches == 3
        assert report.failed_batches == 0


# =============================================================================
# 補助関数
# =============================================================================

class TestSplitIntoBatches:
    def test_exact(self):
        records = [{"i": i} for i in range(10)]
        batches = _split_into_batches(records, 5)
        assert len(batches) == 2
        assert all(len(b) == 5 for b in batches)

    def test_uneven(self):
        records = [{"i": i} for i in range(7)]
        batches = _split_into_batches(records, 3)
        assert len(batches) == 3
        assert len(batches[2]) == 1

    def test_invalid_size(self):
        with pytest.raises(ValueError):
            _split_into_batches([{"i": 1}], 0)


class TestFlattenAndCollectFailures:
    def test_flatten_only_successes(self):
        results = [
            BatchResult(
                batch_index=0, success=True,
                classifications=[{"a": 1}, {"a": 2}],
            ),
            BatchResult(batch_index=1, success=False, error="err"),
            BatchResult(
                batch_index=2, success=True,
                classifications=[{"a": 3}],
            ),
        ]
        flat = flatten_results(results)
        assert flat == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_collect_failed_records(self):
        results = [
            BatchResult(batch_index=0, success=True),
            BatchResult(
                batch_index=1, success=False,
                records=[{"id": "x"}, {"id": "y"}],
            ),
            BatchResult(
                batch_index=2, success=False,
                records=[{"id": "z"}],
            ),
        ]
        failed = collect_failed_records(results)
        assert failed == [{"id": "x"}, {"id": "y"}, {"id": "z"}]


# =============================================================================
# レポート
# =============================================================================

class TestRunReport:
    def test_summary_format(self):
        report = RunReport(
            total_batches=10,
            successful_batches=8,
            failed_batches=2,
            total_input_records=100,
            total_output_records=80,
            total_tokens=15000,
            total_elapsed_seconds=120.5,
            failed_batch_indices=[3, 7],
        )
        s = report.summary()
        assert "総バッチ数" in s
        assert "10" in s
        assert "[3, 7]" in s


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
