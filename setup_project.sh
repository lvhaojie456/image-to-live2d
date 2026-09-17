#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env，请填入 APEXIN_API_KEY。"
fi

mkdir -p outputs work
echo "环境准备完成。"

