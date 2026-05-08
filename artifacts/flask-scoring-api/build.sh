#!/bin/bash
set -e
pip install -r /home/runner/workspace/artifacts/flask-scoring-api/requirements.txt
cd /home/runner/workspace
pnpm install --frozen-lockfile
pnpm --filter @workspace/flask-scoring-api run build
