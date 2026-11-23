#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
if [[ ! -e .env ]]; then cp .env.example .env; chmod 600 .env; fi
printf '\nInstalled. Edit %s/.env and point Pi Bot to %s/.venv/bin/python with arg %s/server.py\n' "$PWD" "$PWD" "$PWD"
