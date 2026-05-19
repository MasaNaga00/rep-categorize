#!/bin/bash
# =============================================================================
# Dify ワークフロー API 動作確認スクリプト
# =============================================================================
# 使い方:
#   1. .env または環境変数で DIFY_BASE_URL と DIFY_API_KEY を設定
#   2. ./examples/dify_curl_test.sh [payload_name]
#      payload_name 未指定時は全テストケースを実行
#
# 例:
#   export DIFY_BASE_URL=https://dify.example.com
#   export DIFY_API_KEY=app-xxxxx
#   ./examples/dify_curl_test.sh 01_ml_simple
# =============================================================================

set -e

# .env があれば読み込み
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 環境変数チェック
if [ -z "$DIFY_BASE_URL" ]; then
    echo "Error: DIFY_BASE_URL is not set"
    echo "Example: export DIFY_BASE_URL=https://dify.example.com"
    exit 1
fi

if [ -z "$DIFY_API_KEY" ]; then
    echo "Error: DIFY_API_KEY is not set"
    echo "Example: export DIFY_API_KEY=app-xxxxx"
    exit 1
fi

PAYLOAD_DIR="docs/dify/test_payloads"
ENDPOINT="${DIFY_BASE_URL}/v1/workflows/run"

run_test() {
    local payload_name=$1
    local payload_file="${PAYLOAD_DIR}/${payload_name}.json"

    if [ ! -f "$payload_file" ]; then
        echo "Error: Payload file not found: $payload_file"
        return 1
    fi

    echo "============================================================"
    echo "Test: $payload_name"
    echo "============================================================"

    local response
    response=$(curl -s -w "\n__HTTP_STATUS__:%{http_code}\n__TIME__:%{time_total}" \
        -X POST "$ENDPOINT" \
        -H "Authorization: Bearer $DIFY_API_KEY" \
        -H "Content-Type: application/json" \
        --data @"$payload_file")

    local body
    local http_status
    local time_total
    body=$(echo "$response" | sed -n '/__HTTP_STATUS__/q;p')
    http_status=$(echo "$response" | grep '__HTTP_STATUS__' | cut -d: -f2)
    time_total=$(echo "$response" | grep '__TIME__' | cut -d: -f2)

    echo "HTTP Status: $http_status"
    echo "Elapsed: ${time_total}s"
    echo ""

    if [ "$http_status" != "200" ]; then
        echo "❌ Test failed"
        echo "Response:"
        echo "$body" | head -50
        echo ""
        return 1
    fi

    # jq があれば整形して表示
    if command -v jq &> /dev/null; then
        echo "Workflow Status: $(echo "$body" | jq -r '.data.status')"
        echo "Total Tokens: $(echo "$body" | jq -r '.data.total_tokens')"
        echo "Elapsed Time (Dify): $(echo "$body" | jq -r '.data.elapsed_time')s"
        echo ""
        echo "Result (LLM output):"
        echo "$body" | jq -r '.data.outputs.result' | jq '.' 2>/dev/null || \
            echo "$body" | jq -r '.data.outputs.result'
    else
        echo "Response (raw):"
        echo "$body" | head -100
    fi

    echo ""
    echo "✅ Test passed"
    echo ""
}

# メイン
if [ $# -eq 0 ]; then
    # 全テスト実行
    echo "Running all test cases..."
    echo ""
    for f in "$PAYLOAD_DIR"/*.json; do
        name=$(basename "$f" .json)
        run_test "$name"
    done
else
    # 個別テスト
    run_test "$1"
fi
