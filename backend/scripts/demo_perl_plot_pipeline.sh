#!/usr/bin/env bash
set -euo pipefail

# Requires: jq, curl, podman, backend server running on localhost:8000
API_BASE="${API_BASE:-http://localhost:8000/api/v1}"

# Default repo; override with argument if you want
REPO_URL="${1:-https://github.com/rlee-boids/perl-plot-project.git}"
REVISION="${REVISION:-main}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/.."

cd "$PROJECT_ROOT"

echo "=== Pipeline starting for repo: $REPO_URL @ $REVISION ==="

# ---------------------------------------------------------------------------
# 1) Analyze repo
# ---------------------------------------------------------------------------
echo "=== [1/7] Analyzing repo via /analyzer/analyze-repo ==="
ANALYZE_PAYLOAD=$(cat <<EOF
{
  "repo_url": "$REPO_URL",
  "language": "perl",
  "revision": "$REVISION",
  "max_files": 50,
  "behavior_domain": "plotting"
}
EOF
)

ANALYZE_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/analyzer/analyze-repo" \
  -H "Content-Type: application/json" \
  -d "$ANALYZE_PAYLOAD" > "$ANALYZE_JSON"

echo "Analyze response saved to $ANALYZE_JSON"
cat "$ANALYZE_JSON"

# Extract key IDs using jq
UI_IMPL_ID=$(jq '.analyzed_files[] | select(.file_path=="cgi-bin/plot_ui.cgi") | .implementation_id' "$ANALYZE_JSON")
LIB_IMPL_ID=$(jq '.analyzed_files[] | select(.file_path=="lib/Plot/Generator.pm") | .implementation_id' "$ANALYZE_JSON")
LIB_BEHAVIOR_ID=$(jq '.analyzed_files[] | select(.file_path=="lib/Plot/Generator.pm") | .behavior_id' "$ANALYZE_JSON")

if [ -z "$UI_IMPL_ID" ] || [ -z "$LIB_IMPL_ID" ] || [ -z "$LIB_BEHAVIOR_ID" ]; then
  echo "ERROR: Could not find expected file entries (cgi-bin/plot_ui.cgi, lib/Plot/Generator.pm)." >&2
  exit 1
fi

echo "UI_IMPL_ID        = $UI_IMPL_ID"
echo "LIB_IMPL_ID       = $LIB_IMPL_ID"
echo "LIB_BEHAVIOR_ID   = $LIB_BEHAVIOR_ID"

# ---------------------------------------------------------------------------
# 2) Convert the library behavior to Python
# ---------------------------------------------------------------------------
echo "=== [2/7] Converting lib/Plot/Generator.pm behavior -> Python via /conversion/convert ==="

TARGET_REPO_NAME="${TARGET_REPO_NAME:-perl-plot-project-python-port}"

CONVERT_PAYLOAD=$(cat <<EOF
{
  "behavior_id": $LIB_BEHAVIOR_ID,
  "source_language": "perl",
  "target_language": "python",
  "target_repo_name": "$TARGET_REPO_NAME"
}
EOF
)

CONVERT_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/conversion/convert" \
  -H "Content-Type: application/json" \
  -d "$CONVERT_PAYLOAD" > "$CONVERT_JSON"

echo "Convert response saved to $CONVERT_JSON"
cat "$CONVERT_JSON"

PY_IMPL_ID=$(jq '.implementation.id' "$CONVERT_JSON")
PY_REPO_URL=$(jq -r '.target_repo_url' "$CONVERT_JSON")

echo "PY_IMPL_ID  = $PY_IMPL_ID"
echo "PY_REPO_URL = $PY_REPO_URL"

# ---------------------------------------------------------------------------
# 3) Build legacy harness for Perl library
# ---------------------------------------------------------------------------
echo "=== [3/7] Building legacy harness via /runtime/build-legacy-harness ==="

HARNESS_PAYLOAD=$(cat <<EOF
{
  "behavior_id": $LIB_BEHAVIOR_ID,
  "language": "perl"
}
EOF
)

HARNESS_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/runtime/build-legacy-harness" \
  -H "Content-Type: application/json" \
  -d "$HARNESS_PAYLOAD" > "$HARNESS_JSON"

echo "Harness response saved to $HARNESS_JSON"
cat "$HARNESS_JSON"

HARNESS_IMPL_ID=$(jq '.harness.id' "$HARNESS_JSON")

echo "HARNESS_IMPL_ID = $HARNESS_IMPL_ID"

# ---------------------------------------------------------------------------
# 4) Run legacy + harness tests (compatibility harness)
# ---------------------------------------------------------------------------
echo "=== [4/7] Running legacy + harness tests via /runtime/run-legacy-with-harness ==="

RUN_COMPAT_PAYLOAD=$(cat <<EOF
{
  "legacy_implementation_id": $LIB_IMPL_ID,
  "harness_implementation_id": $HARNESS_IMPL_ID,
  "behavior_id": $LIB_BEHAVIOR_ID
}
EOF
)

COMPAT_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/runtime/run-legacy-with-harness" \
  -H "Content-Type: application/json" \
  -d "$RUN_COMPAT_PAYLOAD" > "$COMPAT_JSON"

echo "Compat test response saved to $COMPAT_JSON"
cat "$COMPAT_JSON"

# ---------------------------------------------------------------------------
# 5) Build converted tests in the Python repo
# ---------------------------------------------------------------------------
echo "=== [5/7] Generating converted tests (pytest) via /runtime/build-converted-tests ==="

BUILD_CONVERTED_TESTS_PAYLOAD=$(cat <<EOF
{
  "implementation_id": $PY_IMPL_ID
}
EOF
)

CONVERTED_TESTS_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/runtime/build-converted-tests" \
  -H "Content-Type: application/json" \
  -d "$BUILD_CONVERTED_TESTS_PAYLOAD" > "$CONVERTED_TESTS_JSON"

echo "Converted tests response saved to $CONVERTED_TESTS_JSON"
cat "$CONVERTED_TESTS_JSON"

# ---------------------------------------------------------------------------
# 6) Run tests for Python implementation (inside Podman)
# ---------------------------------------------------------------------------
echo "=== [6/7] Running tests for converted Python implementation via /runtime/test-implementation ==="

TEST_PY_PAYLOAD=$(cat <<EOF
{
  "implementation_id": $PY_IMPL_ID
}
EOF
)

TEST_PY_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/runtime/test-implementation" \
  -H "Content-Type: application/json" \
  -d "$TEST_PY_PAYLOAD" > "$TEST_PY_JSON"

echo "Python test run response saved to $TEST_PY_JSON"
cat "$TEST_PY_JSON"

# ---------------------------------------------------------------------------
# 7) Deploy legacy Perl UI as a service
# ---------------------------------------------------------------------------
echo "=== [7/7] Deploying legacy Perl UI via /runtime/deploy-service ==="

DEPLOY_PAYLOAD=$(cat <<EOF
{
  "implementation_id": $UI_IMPL_ID
}
EOF
)

DEPLOY_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/runtime/deploy-service" \
  -H "Content-Type: application/json" \
  -d "$DEPLOY_PAYLOAD" > "$DEPLOY_JSON"

echo "Deploy response saved to $DEPLOY_JSON"
cat "$DEPLOY_JSON"

SERVICE_URL=$(jq -r '.url' "$DEPLOY_JSON")

echo
echo "==============================================="
echo "Pipeline complete ✅"
echo "Legacy Perl UI should be reachable at:"
echo "  $SERVICE_URL"
echo "==============================================="
