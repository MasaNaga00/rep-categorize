"""
dify_client_demo.py
===================
split.py の出力を Dify ワークフローに送信して分類結果を取得する一連デモ。

実行前準備:
    1. .env に DIFY_BASE_URL と DIFY_API_KEY を設定
       例:
         DIFY_BASE_URL=https://dify.example.com
         DIFY_API_KEY=app-xxxxx
    2. Dify ワークフローを構築・公開済み
    3. python-dotenv をインストール: pip install python-dotenv

実行:
    cd repair_failure_classifier
    python3 examples/dify_client_demo.py

API キー未設定の場合はモックモードで動作（実APIを呼ばずダミーレスポンス）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# .env を読み込み（あれば）
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from dify_client import (
    DifyClient,
    DifyClientError,
    flatten_results,
    collect_failed_records,
)
from prompt_builder import RECORD_KEY_ORDER
from split import split_records


# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def make_demo_data() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "repair_id": "DEMO_001", "product_type": "ML",
            "user_comment": "AFが効きません",
            "repair_comment": "AFユニット交換にて復旧",
            "internal_1": "", "internal_2": "",
        },
        {
            "repair_id": "DEMO_002", "product_type": "ML",
            "user_comment": "①AF不良 ②電源不安定",
            "repair_comment": "①AF調整 ②電池接点清掃",
            "internal_1": "", "internal_2": "",
        },
        {
            "repair_id": "DEMO_003", "product_type": "ML",
            "user_comment": "海で使用後、電源入らず",
            "repair_comment": "内部に水濡れ痕、塩化痕確認。基板交換",
            "internal_1": "保証対象外", "internal_2": "",
        },
    ])


def prepare_records_for_dify(split_df: pd.DataFrame) -> list[dict]:
    """
    split.py の DataFrame 出力を Dify に送る形式 (list[dict]) に変換。
    必須キーのみに絞る。
    """
    return [
        {k: row[k] for k in RECORD_KEY_ORDER}
        for _, row in split_df.iterrows()
    ]


def demo_sync(client: DifyClient, records: list[dict]) -> None:
    """同期APIでのデモ実行。"""
    print("=" * 70)
    print("同期API実行（バッチサイズ=2）")
    print("=" * 70)

    results, report = client.run_batches(
        records, product_type="ML", batch_size=2,
    )

    print(report.summary())
    print()

    if report.failed_batches > 0:
        print("⚠️ 失敗したバッチがあります:")
        for r in results:
            if not r.success:
                print(f"  Batch {r.batch_index}: {r.error[:200]}")
        print()
        print("失敗レコードの再実行:")
        failed = collect_failed_records(results)
        print(f"  {len(failed)} レコード")
        # 必要なら client.run_batches(failed, ...) で再実行可能
        print()

    classifications = flatten_results(results)
    print(f"分類結果 {len(classifications)} 件:")
    for c in classifications:
        rid = c.get("repair_id")
        sub = c.get("sub_id")
        user_code = c.get("user_perspective", {}).get("failure_category_code")
        repair_code = c.get("repair_perspective", {}).get("failure_category_code")
        repro = c.get("reproduction_status")
        envs = c.get("environment_factors", [])
        print(
            f"  {rid} (sub_id={sub}): "
            f"user={user_code}, repair={repair_code}, "
            f"repro={repro}, env={envs}"
        )


async def demo_async(client: DifyClient, records: list[dict]) -> None:
    """非同期APIでのデモ実行。"""
    print("=" * 70)
    print("非同期API実行（バッチサイズ=2、並列度=3）")
    print("=" * 70)

    results, report = await client.run_batches_async(
        records, product_type="ML", batch_size=2, max_concurrent=3,
    )

    print(report.summary())


def main():
    # データ準備
    raw_df = make_demo_data()
    print(f"入力レコード: {len(raw_df)} 件")

    split_df, split_report = split_records(raw_df)
    print(f"分割後: {len(split_df)} レコード")
    records = prepare_records_for_dify(split_df)
    print()

    # クライアント初期化
    base_url = os.environ.get("DIFY_BASE_URL")
    api_key = os.environ.get("DIFY_API_KEY")

    if not base_url or not api_key:
        print("=" * 70)
        print("⚠️  DIFY_BASE_URL または DIFY_API_KEY が未設定")
        print("=" * 70)
        print(".env ファイルに以下を設定してください:")
        print("  DIFY_BASE_URL=https://dify.example.com")
        print("  DIFY_API_KEY=app-xxxxx")
        print()
        print("代わりに、Dify に送信する payload のプレビューを表示します:")
        print()
        from dify_client import _build_payload
        payload = _build_payload(records, "ML")
        # 見やすく整形
        preview = {
            "inputs": {
                "records_json": payload["inputs"]["records_json"][:200] + "...",
                "n_records": payload["inputs"]["n_records"],
                "product_type": payload["inputs"]["product_type"],
            },
            "response_mode": payload["response_mode"],
            "user": payload["user"],
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    try:
        client = DifyClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=120,
            max_retries=3,
        )
    except DifyClientError as e:
        print(f"❌ クライアント初期化失敗: {e}")
        return

    # 同期API
    try:
        demo_sync(client, records)
    except DifyClientError as e:
        print(f"❌ 同期API実行失敗: {e}")
        return

    # 非同期API
    print()
    try:
        asyncio.run(demo_async(client, records))
    except DifyClientError as e:
        print(f"❌ 非同期API実行失敗: {e}")


if __name__ == "__main__":
    main()
