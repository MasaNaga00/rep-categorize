"""
dify_client.py
==============
Dify Workflow API クライアント。

責務:
    - Dify ワークフローの実行（blocking モード）
    - 同期・非同期の両APIを提供
    - リトライ処理（tenacity）
    - 失敗バッチを記録しつつ処理継続
    - LLM応答JSONのパース

設計判断（確定）:
    - 認証: 環境変数 (.env) と引数の両対応
    - 同期/非同期: 両方提供
    - response_mode: blocking 固定
    - エラー挙動: リトライ → 失敗マークしてスキップ
    - バッチサイズ縮小: 実装しない（YAGNI）
    - 入出力: list[dict] で統一

Usage:
    # 同期API（Notebook向け）
    from dify_client import DifyClient

    client = DifyClient(base_url="https://dify.example.com")  # APIキーは環境変数から
    results = client.run_batches(records, product_type="ML", batch_size=10)

    # 非同期API（プロダクション向け）
    import asyncio
    results = asyncio.run(client.run_batches_async(
        records, product_type="ML", batch_size=10, max_concurrent=5,
    ))
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import requests
from tenacity import (
    AsyncRetrying,
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 環境変数キー
# =============================================================================

ENV_BASE_URL = "DIFY_BASE_URL"
ENV_API_KEY = "DIFY_API_KEY"
ENV_CA_BUNDLE = "DIFY_CA_BUNDLE"


# =============================================================================
# 例外
# =============================================================================

class DifyClientError(Exception):
    """Dify クライアントの基底例外。"""
    pass


class DifyAuthError(DifyClientError):
    """認証失敗（401, 403 等）。リトライしない。"""
    pass


class DifyAPIError(DifyClientError):
    """Dify API からの 4xx/5xx エラー（認証以外）。"""
    pass


class DifyTimeoutError(DifyClientError):
    """タイムアウト。リトライ対象。"""
    pass


class DifyResponseParseError(DifyClientError):
    """LLM応答のJSONパース失敗。リトライ対象。"""
    pass


# 「リトライしてリカバリ可能」な例外群
RETRYABLE_EXCEPTIONS = (
    DifyTimeoutError,
    DifyResponseParseError,
    DifyAPIError,  # 5xx 系などはリトライ価値あり
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    aiohttp.ClientError,
    asyncio.TimeoutError,
)


# =============================================================================
# レスポンス・バッチ結果のデータ構造
# =============================================================================

@dataclass
class BatchResult:
    """1バッチの実行結果。成功 or 失敗の両方を表現。"""
    batch_index: int                          # バッチ番号（0始まり）
    success: bool                             # 成功か失敗か
    records: list[dict[str, Any]] = field(default_factory=list)  # 入力レコード
    classifications: list[dict[str, Any]] = field(default_factory=list)  # LLMの分類結果
    error: str | None = None                  # 失敗時のエラーメッセージ
    elapsed_seconds: float = 0.0
    total_tokens: int | None = None


@dataclass
class RunReport:
    """全バッチ実行の集計レポート。"""
    total_batches: int = 0
    successful_batches: int = 0
    failed_batches: int = 0
    total_input_records: int = 0
    total_output_records: int = 0
    total_tokens: int = 0
    total_elapsed_seconds: float = 0.0
    failed_batch_indices: list[int] = field(default_factory=list)

    def summary(self) -> str:
        return "\n".join([
            "=" * 50,
            "Dify 実行レポート",
            "=" * 50,
            f"総バッチ数         : {self.total_batches}",
            f"  成功            : {self.successful_batches}",
            f"  失敗            : {self.failed_batches}",
            f"入力レコード総数   : {self.total_input_records}",
            f"出力レコード総数   : {self.total_output_records}",
            f"使用トークン総数   : {self.total_tokens:,}",
            f"総処理時間        : {self.total_elapsed_seconds:.2f} 秒",
            f"失敗バッチ番号    : {self.failed_batch_indices}",
            "=" * 50,
        ])


# =============================================================================
# レスポンスパーサ
# =============================================================================

def _parse_dify_response(response_data: dict[str, Any]) -> tuple[str, dict]:
    """
    Dify API レスポンスから LLM応答文字列とメタ情報を取り出す。

    Args:
        response_data: Dify Workflow API のJSONレスポンス全体

    Returns:
        (result_text, metadata)
        result_text: LLMが返した文字列（JSON配列の文字列）
        metadata: {"total_tokens": int, "elapsed_time": float}

    Raises:
        DifyAPIError: レスポンス構造が想定と異なる
        DifyClientError: ワークフロー実行が失敗ステータス
    """
    if "data" not in response_data:
        raise DifyAPIError(
            f"Difyレスポンスに 'data' フィールドがありません: {response_data}"
        )

    data = response_data["data"]
    status = data.get("status")
    if status != "succeeded":
        error = data.get("error", "(no error message)")
        raise DifyClientError(
            f"ワークフロー実行失敗: status={status}, error={error}"
        )

    outputs = data.get("outputs", {})
    result_text = outputs.get("result")
    if not isinstance(result_text, str):
        raise DifyAPIError(
            f"outputs.result が文字列ではありません: {type(result_text).__name__}"
        )

    metadata = {
        "total_tokens": data.get("total_tokens", 0),
        "elapsed_time": data.get("elapsed_time", 0.0),
    }
    return result_text, metadata


def _parse_llm_classifications(result_text: str) -> list[dict[str, Any]]:
    """
    LLMが返した文字列からJSON配列をパース。

    LLMが余計な前置きやコードフェンスを付けた場合のリカバリも試みる。

    Args:
        result_text: LLMが返した文字列

    Returns:
        分類結果のlist[dict]

    Raises:
        DifyResponseParseError: パース不能
    """
    text = result_text.strip()

    # コードフェンスを剥がす（```json ... ``` の場合）
    if text.startswith("```"):
        # 最初の改行までを除去（``` または ```json）
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # 末尾の ``` を除去
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()

    # 通常のパース試行
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise DifyResponseParseError(
            f"LLM応答のJSONパース失敗: {e}\n応答先頭500文字: {text[:500]}"
        ) from e

    # 期待形式は配列
    if isinstance(parsed, list):
        return parsed

    # JSON Mode で {"results": [...]} のように包まれている場合のフォールバック
    if isinstance(parsed, dict):
        for key in ("results", "data", "classifications", "records"):
            if key in parsed and isinstance(parsed[key], list):
                logger.info(
                    f"JSON応答が配列ではなく {{'{key}': [...]}} 形式でした。"
                    f"自動的に配列を取り出しました。"
                )
                return parsed[key]

    raise DifyResponseParseError(
        f"LLM応答が配列でもラップされたオブジェクトでもありません: "
        f"type={type(parsed).__name__}"
    )


# =============================================================================
# ペイロード構築
# =============================================================================

def _build_payload(
    records: list[dict[str, Any]],
    product_type: str,
    user: str = "python_client",
) -> dict[str, Any]:
    """
    Dify Workflow API 実行用のペイロードを構築。

    レコードJSON文字列化、件数算出、変数バインディングを行う。
    """
    if not records:
        raise ValueError("records is empty")

    if product_type not in ("ML", "LENS"):
        raise ValueError(f"product_type must be 'ML' or 'LENS': got {product_type!r}")

    records_json = json.dumps(records, ensure_ascii=False, indent=2)
    return {
        "inputs": {
            "records_json": records_json,
            "n_records": len(records),
            "product_type": product_type,
        },
        "response_mode": "blocking",
        "user": user,
    }


# =============================================================================
# DifyClient
# =============================================================================

@dataclass
class DifyClient:
    """
    Dify Workflow API クライアント。

    同期・非同期の両方の実行APIを提供する。
    """

    base_url: str | None = None         # 例: "https://dify.example.com"
    api_key: str | None = None          # 例: "app-xxxxx"
    ca_bundle: str | Path | None = None  # SSL CAバンドル/証明書のパス。Noneなら環境変数を見る
    timeout_seconds: int = 120          # 1リクエストのタイムアウト
    max_retries: int = 3                # リトライ回数
    user_identifier: str = "python_client"  # Difyログに残るユーザ識別子

    def __post_init__(self) -> None:
        # 環境変数からのフォールバック
        if self.base_url is None:
            self.base_url = os.environ.get(ENV_BASE_URL)
        if self.api_key is None:
            self.api_key = os.environ.get(ENV_API_KEY)
        if self.ca_bundle is None:
            env_ca = os.environ.get(ENV_CA_BUNDLE)
            # 空文字列は未指定扱い
            if env_ca:
                self.ca_bundle = env_ca

        if not self.base_url:
            raise DifyClientError(
                f"base_url が指定されていません。"
                f"引数または環境変数 {ENV_BASE_URL} で指定してください。"
            )
        if not self.api_key:
            raise DifyClientError(
                f"api_key が指定されていません。"
                f"引数または環境変数 {ENV_API_KEY} で指定してください。"
            )

        # 末尾スラッシュを除去
        self.base_url = self.base_url.rstrip("/")

        # SSL設定の解決
        # - ca_bundle が None        → デフォルト検証 (verify=True)
        # - ca_bundle にパス指定あり → そのファイルを使用 (存在チェックする)
        if self.ca_bundle is None:
            self._verify: bool | str = True
            self._ssl_context: ssl.SSLContext | None = None
        else:
            ca_path = Path(self.ca_bundle)
            if not ca_path.exists():
                raise DifyClientError(
                    f"CA bundle ファイルが見つかりません: {ca_path}\n"
                    f"引数 ca_bundle または環境変数 {ENV_CA_BUNDLE} のパスを確認してください。"
                )
            if not ca_path.is_file():
                raise DifyClientError(
                    f"CA bundle のパスがファイルではありません: {ca_path}"
                )
            ca_path_str = str(ca_path)
            # 同期側 (requests) はパス文字列を verify に渡す
            self._verify = ca_path_str
            # 非同期側 (aiohttp) は SSLContext を作って TCPConnector に渡す
            self._ssl_context = ssl.create_default_context(cafile=ca_path_str)
            logger.info(f"Using custom CA bundle: {ca_path_str}")

    # =========================================================================
    # 共通: HTTPヘッダ
    # =========================================================================

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_connector(self) -> aiohttp.TCPConnector | None:
        """
        非同期APIで使うTCPConnectorを生成。

        カスタムCA bundle が指定されている場合は SSLContext を持つ
        TCPConnector を返す。指定がない場合は None を返す（aiohttp の
        デフォルト挙動: certifi のCAバンドルで検証）。

        Note:
            ClientSession に渡すコネクタは Session ごとに新規作成する必要が
            ある（コネクタは Session のライフサイクルに紐づくため）。
        """
        if self._ssl_context is None:
            return None
        return aiohttp.TCPConnector(ssl=self._ssl_context)

    @property
    def workflow_run_url(self) -> str:
        return f"{self.base_url}/v1/workflows/run"

    # =========================================================================
    # 同期 API
    # =========================================================================

    def run_workflow(
        self,
        records: list[dict[str, Any]],
        product_type: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        1バッチを実行し、分類結果とメタ情報を返す（同期、リトライあり）。

        Returns:
            (classifications, metadata)
            classifications: LLMの分類結果のlist[dict]
            metadata: {"total_tokens": int, "elapsed_time": float}

        Raises:
            DifyAuthError: 認証エラー（リトライしない）
            DifyClientError: 全リトライが失敗
        """
        payload = _build_payload(records, product_type, self.user_identifier)

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self.max_retries),
                wait=wait_exponential(multiplier=1, min=2, max=30),
                retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    return self._call_workflow_once(payload)
        except RetryError as e:
            raise DifyClientError(
                f"全リトライ ({self.max_retries}回) 失敗: {e}"
            ) from e

    def _call_workflow_once(
        self, payload: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """1回の同期呼び出し（リトライなし）。"""
        try:
            resp = requests.post(
                self.workflow_run_url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
                verify=self._verify,
            )
        except requests.exceptions.Timeout as e:
            raise DifyTimeoutError(f"タイムアウト: {e}") from e

        if resp.status_code in (401, 403):
            raise DifyAuthError(
                f"認証エラー: HTTP {resp.status_code}\n{resp.text[:500]}"
            )
        if not resp.ok:
            raise DifyAPIError(
                f"HTTPエラー: {resp.status_code}\n{resp.text[:500]}"
            )

        data = resp.json()
        result_text, metadata = _parse_dify_response(data)
        classifications = _parse_llm_classifications(result_text)
        return classifications, metadata

    def run_batches(
        self,
        records: list[dict[str, Any]],
        product_type: str,
        batch_size: int = 10,
    ) -> tuple[list[BatchResult], RunReport]:
        """
        レコードをバッチ分割して順次実行（同期）。

        失敗したバッチはBatchResult.success=Falseで記録、処理継続。

        Returns:
            (batch_results, report)
        """
        batches = _split_into_batches(records, batch_size)
        results: list[BatchResult] = []
        report = RunReport(
            total_batches=len(batches),
            total_input_records=len(records),
        )

        for i, batch in enumerate(batches):
            logger.info(f"Running batch {i + 1}/{len(batches)} ({len(batch)} records)...")
            try:
                classifications, metadata = self.run_workflow(batch, product_type)
                br = BatchResult(
                    batch_index=i,
                    success=True,
                    records=batch,
                    classifications=classifications,
                    elapsed_seconds=metadata.get("elapsed_time", 0.0),
                    total_tokens=metadata.get("total_tokens"),
                )
                report.successful_batches += 1
                report.total_output_records += len(classifications)
                if br.total_tokens:
                    report.total_tokens += br.total_tokens
                report.total_elapsed_seconds += br.elapsed_seconds
            except DifyAuthError:
                # 認証エラーは即座に止める
                raise
            except Exception as e:
                logger.error(f"Batch {i} failed: {e}")
                br = BatchResult(
                    batch_index=i, success=False, records=batch, error=str(e),
                )
                report.failed_batches += 1
                report.failed_batch_indices.append(i)
            results.append(br)

        return results, report

    # =========================================================================
    # 非同期 API
    # =========================================================================

    async def run_workflow_async(
        self,
        records: list[dict[str, Any]],
        product_type: str,
        session: aiohttp.ClientSession | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        1バッチを非同期実行（リトライあり）。

        Args:
            session: 既存のClientSession（複数並列実行で使い回す場合に指定）

        Returns:
            (classifications, metadata)
        """
        payload = _build_payload(records, product_type, self.user_identifier)

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_retries),
                wait=wait_exponential(multiplier=1, min=2, max=30),
                retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    return await self._call_workflow_once_async(payload, session)
        except RetryError as e:
            raise DifyClientError(
                f"全リトライ ({self.max_retries}回) 失敗: {e}"
            ) from e

    async def _call_workflow_once_async(
        self,
        payload: dict[str, Any],
        session: aiohttp.ClientSession | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """1回の非同期呼び出し（リトライなし）。"""
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession(connector=self._build_connector())

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            try:
                async with session.post(
                    self.workflow_run_url,
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                ) as resp:
                    if resp.status in (401, 403):
                        text = await resp.text()
                        raise DifyAuthError(
                            f"認証エラー: HTTP {resp.status}\n{text[:500]}"
                        )
                    if resp.status >= 400:
                        text = await resp.text()
                        raise DifyAPIError(
                            f"HTTPエラー: {resp.status}\n{text[:500]}"
                        )
                    data = await resp.json()
            except asyncio.TimeoutError as e:
                raise DifyTimeoutError(f"タイムアウト: {e}") from e

            result_text, metadata = _parse_dify_response(data)
            classifications = _parse_llm_classifications(result_text)
            return classifications, metadata
        finally:
            if own_session:
                await session.close()

    async def run_batches_async(
        self,
        records: list[dict[str, Any]],
        product_type: str,
        batch_size: int = 10,
        max_concurrent: int = 5,
    ) -> tuple[list[BatchResult], RunReport]:
        """
        レコードをバッチ分割して並列実行（非同期）。

        Args:
            max_concurrent: 同時実行する最大バッチ数（セマフォで制御）

        Returns:
            (batch_results, report)
        """
        batches = _split_into_batches(records, batch_size)
        report = RunReport(
            total_batches=len(batches),
            total_input_records=len(records),
        )

        semaphore = asyncio.Semaphore(max_concurrent)

        async with aiohttp.ClientSession(connector=self._build_connector()) as session:
            async def run_one(idx: int, batch: list[dict]) -> BatchResult:
                async with semaphore:
                    logger.info(
                        f"Running batch {idx + 1}/{len(batches)} ({len(batch)} records)..."
                    )
                    try:
                        classifications, metadata = await self.run_workflow_async(
                            batch, product_type, session,
                        )
                        return BatchResult(
                            batch_index=idx,
                            success=True,
                            records=batch,
                            classifications=classifications,
                            elapsed_seconds=metadata.get("elapsed_time", 0.0),
                            total_tokens=metadata.get("total_tokens"),
                        )
                    except DifyAuthError:
                        raise
                    except Exception as e:
                        logger.error(f"Batch {idx} failed: {e}")
                        return BatchResult(
                            batch_index=idx, success=False, records=batch, error=str(e),
                        )

            tasks = [run_one(i, batch) for i, batch in enumerate(batches)]
            results = await asyncio.gather(*tasks, return_exceptions=False)

        # レポート集計
        for br in results:
            if br.success:
                report.successful_batches += 1
                report.total_output_records += len(br.classifications)
                if br.total_tokens:
                    report.total_tokens += br.total_tokens
                report.total_elapsed_seconds += br.elapsed_seconds
            else:
                report.failed_batches += 1
                report.failed_batch_indices.append(br.batch_index)

        return results, report


# =============================================================================
# 補助関数
# =============================================================================

def _split_into_batches(
    records: list[dict[str, Any]], batch_size: int,
) -> list[list[dict[str, Any]]]:
    """レコードを指定サイズのバッチに分割。"""
    if batch_size <= 0:
        raise ValueError("batch_size は1以上")
    return [
        records[i:i + batch_size]
        for i in range(0, len(records), batch_size)
    ]


def flatten_results(batch_results: list[BatchResult]) -> list[dict[str, Any]]:
    """
    全バッチの分類結果を1つのlist[dict]にフラット化。

    成功バッチの classifications だけを連結する。
    後段で DataFrame 化する際に使う。
    """
    flat = []
    for br in batch_results:
        if br.success:
            flat.extend(br.classifications)
    return flat


def collect_failed_records(
    batch_results: list[BatchResult],
) -> list[dict[str, Any]]:
    """
    失敗バッチの入力レコードを集約。

    再実行用などに使う。
    """
    failed = []
    for br in batch_results:
        if not br.success:
            failed.extend(br.records)
    return failed
