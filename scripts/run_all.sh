#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f ".venv/bin/activate" ]]; then
  source ".venv/bin/activate"
elif [[ -f "venv/bin/activate" ]]; then
  source "venv/bin/activate"
elif [[ -f ".venv/Scripts/activate" ]]; then
  source ".venv/Scripts/activate"
elif [[ -f "venv/Scripts/activate" ]]; then
  source "venv/Scripts/activate"
fi

if [[ ! -f "data/edges.csv" ]]; then
  python data/prepare_dataset.py
fi

python -m harness.runner "$@"
python -m harness.make_charts
