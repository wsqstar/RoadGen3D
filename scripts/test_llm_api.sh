#!/bin/bash
# Test LLM API connectivity
#
# Usage:
#   ./test_llm_api.sh                    # Test with default model
#   ./test_llm_api.sh gpt-4o-mini       # Test with specific model
#   ./test_llm_api.sh --list            # List available models

# Load environment variables
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

set -a
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
else
    echo "Error: .env not found at $ENV_FILE"
    exit 1
fi
set +a

ENDPOINT="${GRAPHRAG_API_BASE:-https://api.zetatechs.com/v1/}chat/completions"
KEY="${GRAPHRAG_API_KEY:-}"
DEFAULT_MODEL="gpt-4o-mini"
MODEL="${1:-$DEFAULT_MODEL}"

if [ -z "$KEY" ]; then
    echo "Error: GRAPHRAG_API_KEY not set in .env"
    exit 1
fi

if [ "$1" = "--list" ]; then
    echo "Available models from $ENDPOINT:"
    curl -s -X GET "${GRAPHRAG_API_BASE:-https://api.zetatechs.com/v1/}models" \
      -H "Authorization: Bearer $KEY" 2>/dev/null | \
      python3 -c "import sys,json; data=json.load(sys.stdin); [print('  -', m['id']) for m in data.get('data',[])]" 2>/dev/null || \
      echo "  (Failed to parse models)"
    exit 0
fi

echo "Testing: $ENDPOINT"
echo "Model: $MODEL"
echo ""

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$ENDPOINT" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":10}")

HTTP_CODE=$(echo "$RESPONSE" | tail -c 4)
BODY=$(echo "$RESPONSE" | sed '$ d')

echo "HTTP Status: $HTTP_CODE"
if [ -n "$BODY" ]; then
    echo "Response: $BODY"
fi

if [ "$HTTP_CODE" = "200" ]; then
    echo ""
    echo "API is working"
    exit 0
else
    echo ""
    echo "API returned error"
    exit 1
fi
