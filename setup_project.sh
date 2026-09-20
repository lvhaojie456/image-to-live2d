#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env，请填入 LLM_API_KEY、IMAGE_MODEL、PLANNER_MODEL 与 REMOTE_SSH_HOST。"
fi

mkdir -p outputs work
echo "环境准备完成。"

