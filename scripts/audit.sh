#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$DIR/audit-results"
mkdir -p "$OUT"

echo "=== nanoCursor Repository Audit ==="
echo "Started: $(date)"
echo ""

# 1. Ruff lint
echo "[1/7] Running ruff check..."
cd "$DIR"
ruff check . --output-format=text > "$OUT/ruff.txt" 2>&1 || true
RUFF_COUNT=$(wc -l < "$OUT/ruff.txt" | tr -d ' ')
echo "  -> $RUFF_COUNT lint issues found"

# 2. Mypy type check
echo "[2/7] Running mypy..."
mypy src/ --ignore-missing-imports > "$OUT/mypy.txt" 2>&1 || true
MYPY_ERRORS=$(grep -c "error:" "$OUT/mypy.txt" 2>/dev/null || echo 0)
echo "  -> $MYPY_ERRORS type errors found"

# 3. Pytest coverage
echo "[3/7] Running pytest with coverage..."
pytest --cov=src --cov-report=term-missing --cov-report=json:"$OUT/coverage.json" > "$OUT/pytest.txt" 2>&1 || true
COVERAGE=$(grep "TOTAL" "$OUT/pytest.txt" | awk '{print $NF}' || echo "N/A")
echo "  -> Coverage: $COVERAGE"

# 4. Frontend check
echo "[4/7] Running frontend checks..."
cd "$DIR/frontend"
npm run check > "$OUT/frontend-check.txt" 2>&1 || true
echo "  -> Done (see frontend-check.txt)"

# 5. Python dependency audit
echo "[5/7] Auditing Python dependencies..."
cd "$DIR"
pip-audit > "$OUT/pip-audit.txt" 2>&1 || pip install pip-audit && pip-audit > "$OUT/pip-audit.txt" 2>&1 || true
VULN_COUNT=$(grep -c "found in" "$OUT/pip-audit.txt" 2>/dev/null || echo 0)
echo "  -> $VULN_COUNT known vulnerabilities"

# 6. npm audit
echo "[6/7] Auditing npm dependencies..."
cd "$DIR/frontend"
npm audit > "$OUT/npm-audit.txt" 2>&1 || true
echo "  -> Done (see npm-audit.txt)"

# 7. Git & config checks
echo "[7/7] Running git and config checks..."
cd "$DIR"

# Check for committed artifacts
echo "--- Committed artifacts ---" > "$OUT/git-checks.txt"
find . -name "*.db" -not -path "./.git/*" -not -path "./node_modules/*" >> "$OUT/git-checks.txt" 2>/dev/null || true
find . -name "*.pyc" -not -path "./.git/*" -not -path "./node_modules/*" >> "$OUT/git-checks.txt" 2>/dev/null || true
find . -name "__pycache__" -type d -not -path "./.git/*" -not -path "./node_modules/*" >> "$OUT/git-checks.txt" 2>/dev/null || true

# Check for secrets patterns
echo "--- Potential secrets ---" >> "$OUT/git-checks.txt"
grep -rn --include="*.py" --include="*.js" --include="*.jsx" --include="*.env*" \
  -E "(password|secret|api_key|token)\s*=\s*['\"][^'\"]{8,}" \
  --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git \
  . >> "$OUT/git-checks.txt" 2>/dev/null || true

# Check duplicate configs
echo "--- Duplicate config check ---" >> "$OUT/git-checks.txt"
if [ -f pytest.ini ] && grep -q "pytest" pyproject.toml 2>/dev/null; then
  echo "WARNING: pytest config in both pytest.ini and pyproject.toml" >> "$OUT/git-checks.txt"
fi

# Check .gitignore completeness
echo "--- .gitignore missing patterns ---" >> "$OUT/git-checks.txt"
for pattern in "*.pyc" "__pycache__" ".env" "*.db" "node_modules" ".venv" "dist" "*.egg-info"; do
  if ! grep -q "$pattern" .gitignore 2>/dev/null; then
    echo "MISSING from .gitignore: $pattern" >> "$OUT/git-checks.txt"
  fi
done

GIT_ISSUES=$(grep -c "WARNING\|MISSING" "$OUT/git-checks.txt" 2>/dev/null || echo 0)
echo "  -> $GIT_ISSUES config/git issues found"

echo ""
echo "=== Audit Complete ==="
echo "Results in: $OUT/"
echo "  ruff.txt          - Lint findings"
echo "  mypy.txt          - Type errors"
echo "  pytest.txt        - Test results + coverage"
echo "  coverage.json     - Coverage data"
echo "  frontend-check.txt - Frontend checks"
echo "  pip-audit.txt     - Python vulnerabilities"
echo "  npm-audit.txt     - npm vulnerabilities"
echo "  git-checks.txt    - Config and git issues"
