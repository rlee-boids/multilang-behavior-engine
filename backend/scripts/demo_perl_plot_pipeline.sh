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
echo "=== [1/8] Analyzing repo via /analyzer/analyze-repo ==="
ANALYZE_PAYLOAD=$(cat <<EOF
{
  "repo_url": "$REPO_URL",
  "language": "perl",
  "revision": "$REVISION",
  "max_files": 200,
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

# ---------------------------------------------------------------------------
# Extract IDs WITHOUT hardcoding full paths
# ---------------------------------------------------------------------------

# 1) UI implementation = first file whose path looks CGI-ish
UI_IMPL_ID=$(
  jq '
    .analyzed_files[]
    | select(
        (.file_path | contains("cgi-bin/"))
        or (.file_path | test("cgi"; "i"))
      )
    | .implementation_id
  ' "$ANALYZE_JSON" | head -n 1
)

# 2) Library implementation = first file under lib/ with a .pm extension
LIB_IMPL_ID=$(
  jq '
    .analyzed_files[]
    | select(
        (.file_path | startswith("lib/"))
        and (.file_path | endswith(".pm"))
      )
    | .implementation_id
  ' "$ANALYZE_JSON" | head -n 1
)

# 3) Library behavior_id (same filter as above)
LIB_BEHAVIOR_ID=$(
  jq '
    .analyzed_files[]
    | select(
        (.file_path | startswith("lib/"))
        and (.file_path | endswith(".pm"))
      )
    | .behavior_id
  ' "$ANALYZE_JSON" | head -n 1
)

if [ -z "${UI_IMPL_ID:-}" ] || [ -z "${LIB_IMPL_ID:-}" ] || [ -z "${LIB_BEHAVIOR_ID:-}" ]; then
  echo "ERROR: Could not identify UI or library implementations from analyzed_files." >&2
  echo "Check .analyzed_files in $ANALYZE_JSON for unexpected layout." >&2
  exit 1
fi

echo "UI_IMPL_ID        = $UI_IMPL_ID"
echo "LIB_IMPL_ID       = $LIB_IMPL_ID"
echo "LIB_BEHAVIOR_ID   = $LIB_BEHAVIOR_ID"

# ---------------------------------------------------------------------------
# 2) Build legacy harness for Perl library
# ---------------------------------------------------------------------------
echo "=== [2/8] Building legacy harness via /runtime/build-legacy-harness ==="

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
if [ -z "${HARNESS_IMPL_ID:-}" ]; then
  echo "ERROR: No harness.id found in build-legacy-harness response" >&2
  exit 1
fi

echo "HARNESS_IMPL_ID = $HARNESS_IMPL_ID"

# ---------------------------------------------------------------------------
# 3) Run legacy + harness tests (compatibility harness)
# ---------------------------------------------------------------------------
echo "=== [3/8] Running legacy + harness tests via /runtime/run-legacy-with-harness ==="

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
# 4) Full-project conversion Perl -> Python via /conversion/convert-project
# ---------------------------------------------------------------------------

echo "=== [4/8] Converting FULL project Perl -> Python via /conversion/convert-project ==="

TARGET_REPO_NAME="${TARGET_REPO_NAME:-perl-plot-project-python-port}"

CONVERT_PROJECT_PAYLOAD=$(cat <<EOF
{
  "source_repo_url": "$REPO_URL",
  "source_revision": "$REVISION",
  "source_language": "perl",
  "target_language": "python",
  "target_repo_name": "$TARGET_REPO_NAME"
}
EOF
)

CONVERT_PROJECT_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/conversion/convert-project" \
  -H "Content-Type: application/json" \
  -d "$CONVERT_PROJECT_PAYLOAD" > "$CONVERT_PROJECT_JSON"

echo "Full-project conversion response saved to $CONVERT_PROJECT_JSON"
cat "$CONVERT_PROJECT_JSON"

PY_TARGET_REPO_URL=$(jq -r '.target_repo_url' "$CONVERT_PROJECT_JSON")

# ---------------------------------------------------------------------------
# Extract Python implementations from .implementations[] in the response
# ---------------------------------------------------------------------------

# Python LIB impl = file under lib/ ending in .py
PY_LIB_IMPL_ID=$(
  jq '
    .implementations[]
    | select(
        (.file_path | startswith("lib/"))
        and (.file_path | endswith(".py"))
      )
    | .id
  ' "$CONVERT_PROJECT_JSON" | head -n 1
)

PY_LIB_BEHAVIOR_ID=$(
  jq '
    .implementations[]
    | select(
        (.file_path | startswith("lib/"))
        and (.file_path | endswith(".py"))
      )
    | .behavior_id
  ' "$CONVERT_PROJECT_JSON" | head -n 1
)

# Python UI impl = anything that looks like the converted UI; your current
# conversion uses app/ui/plot_ui.py, but we match generically.
PY_UI_IMPL_ID=$(
  jq '
    .implementations[]
    | select(
        (.file_path | contains("plot_ui.py"))
        or (.file_path | contains("app/ui/"))
        or (.file_path | contains("ui/plot"))
      )
    | .id
  ' "$CONVERT_PROJECT_JSON" | head -n 1
)

echo "PY_TARGET_REPO_URL = $PY_TARGET_REPO_URL"
echo "PY_LIB_IMPL_ID     = ${PY_LIB_IMPL_ID:-<none>}"
echo "PY_LIB_BEHAVIOR_ID = ${PY_LIB_BEHAVIOR_ID:-<none>}"
echo "PY_UI_IMPL_ID      = ${PY_UI_IMPL_ID:-<none>}"

if [ -z "${PY_LIB_IMPL_ID:-}" ] || [ -z "${PY_LIB_BEHAVIOR_ID:-}" ]; then
  echo "ERROR: Could not identify Python library implementation from /conversion/convert-project." >&2
  exit 1
fi

# Python UI is optional; we only treat it as an error if you really want UI.
if [ -z "${PY_UI_IMPL_ID:-}" ]; then
  echo "WARNING: No Python UI implementation found in conversion mapping; Python UI deployment will be skipped." >&2
fi

# ---------------------------------------------------------------------------
# 5) Build converted tests in the Python repo (library)
# ---------------------------------------------------------------------------

echo "=== [5/8] Generating converted tests (pytest) via /runtime/build-converted-tests ==="

BUILD_CONVERTED_TESTS_PAYLOAD=$(cat <<EOF
{
  "implementation_id": $PY_LIB_IMPL_ID
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
# 6) Run tests for Python library implementation (inside Podman)
# ---------------------------------------------------------------------------

echo "=== [6/8] Running tests for converted Python library via /runtime/test-implementation ==="

TEST_PY_LIB_PAYLOAD=$(cat <<EOF
{
  "implementation_id": $PY_LIB_IMPL_ID
}
EOF
)

TEST_PY_LIB_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/runtime/test-implementation" \
  -H "Content-Type: application/json" \
  -d "$TEST_PY_LIB_PAYLOAD" > "$TEST_PY_LIB_JSON"

echo "Python library test run response saved to $TEST_PY_LIB_JSON"
cat "$TEST_PY_LIB_JSON"

# ---------------------------------------------------------------------------
# 7) Deploy legacy Perl UI as a service
# ---------------------------------------------------------------------------

echo "=== [7/8] Deploying legacy Perl UI via /runtime/deploy-service ==="

DEPLOY_PERL_PAYLOAD=$(cat <<EOF
{
  "implementation_id": $UI_IMPL_ID
}
EOF
)

DEPLOY_PERL_JSON="$(mktemp)"
curl -sS -X POST "$API_BASE/runtime/deploy-service" \
  -H "Content-Type: application/json" \
  -d "$DEPLOY_PERL_PAYLOAD" > "$DEPLOY_PERL_JSON"

echo "Perl UI deploy response saved to $DEPLOY_PERL_JSON"
cat "$DEPLOY_PERL_JSON"

PERL_SERVICE_URL=$(jq -r '.url // empty' "$DEPLOY_PERL_JSON")

# ---------------------------------------------------------------------------
# 8) Deploy converted Python UI as a service (if we found one)
# ---------------------------------------------------------------------------

PY_SERVICE_URL=""
if [ -n "${PY_UI_IMPL_ID:-}" ]; then
  echo "=== [8/8] Deploying Python UI via /runtime/deploy-service ==="

  DEPLOY_PY_PAYLOAD=$(cat <<EOF
{
  "implementation_id": $PY_UI_IMPL_ID
}
EOF
)

  DEPLOY_PY_JSON="$(mktemp)"
  curl -sS -X POST "$API_BASE/runtime/deploy-service" \
    -H "Content-Type: application/json" \
    -d "$DEPLOY_PY_PAYLOAD" > "$DEPLOY_PY_JSON"

  echo "Python UI deploy response saved to $DEPLOY_PY_JSON"
  cat "$DEPLOY_PY_JSON"

  PY_SERVICE_URL=$(jq -r '.url // empty' "$DEPLOY_PY_JSON")
else
  echo "Skipping Python UI deployment because no PY_UI_IMPL_ID was discovered."
fi

echo
echo "==============================================="
echo "Pipeline complete"
echo "Legacy Perl UI should be reachable at:"
echo "  ${PERL_SERVICE_URL:-<unknown>}"
echo
echo "Converted Python library repo:"
echo "  ${PY_TARGET_REPO_URL:-<unknown>}"
echo
if [ -n "${PY_SERVICE_URL:-}" ]; then
  echo "Converted Python UI should be reachable at:"
  echo "  $PY_SERVICE_URL"
else
  echo "Converted Python UI was not deployed (PY_UI_IMPL_ID empty)."
fi
echo "==============================================="
