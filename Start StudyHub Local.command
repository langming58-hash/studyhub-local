#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 server.py serve --port 8765 --open --no-scan
