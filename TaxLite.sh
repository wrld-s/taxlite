#!/bin/bash
cd "$(dirname "$0")"

# ── Find Python ───────────────────────────────────────────
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo ""
    echo "  Python not found. Installing via Homebrew..."
    echo ""
    if ! command -v brew &>/dev/null; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    brew install python3
    PYTHON=python3
fi

# ── Install dependencies (first run only) ─────────────────
if [ ! -f ".installed" ]; then
    echo "  First run — installing dependencies..."
    $PYTHON -m pip install --upgrade pip --quiet 2>/dev/null
    $PYTHON -m pip install -r requirements.txt --quiet
    if [ $? -ne 0 ]; then
        echo "  ERROR: Failed to install dependencies."
        exit 1
    fi
    touch .installed
    echo "  Setup complete!"
fi

# ── Launch ────────────────────────────────────────────────
echo ""
echo "  ============================================"
echo "      TaxLite is starting..."
echo "      Opening http://localhost:8501"
echo "      Press Ctrl+C to stop."
echo "  ============================================"
echo ""
$PYTHON -m streamlit run app.py --server.maxUploadSize 50
