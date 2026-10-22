#!/usr/bin/env bash
# Run the full pipeline. From the repo root:  bash scripts/run_all.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/01_fetch_data.py
python3 scripts/02_preprocess.py
python3 scripts/03_analysis.py
python3 scripts/04_export_outputs.py
echo "Done. See outputs/."
