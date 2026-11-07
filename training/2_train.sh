#!/usr/bin/env bash
# Step 2: train your model (LoRA) on the examples from step 1. Lumelle keeps working meanwhile,
# but the Mac will be busy: close heavy apps; about 30–90 minutes for Qwen3-1.7B.
. "$(dirname "$0")/common.sh"
need_mac
[ -f data/train.jsonl ] || { echo "No data yet: run step 1 first (../.venv/bin/python 1_collect.py)"; exit 1; }
activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet --default-timeout=120 --retries 10 "mlx-lm>=0.24"

N=$(wc -l < data/train.jsonl | tr -d ' ')
ITERS="${ITERS:-$(( N * 3 / 2 ))}"           # ≈ 3 passes over the data with batch size 2
[ "$ITERS" -lt 200 ] && ITERS=200
echo "Base model: $BASE_MODEL   examples: $N   steps: $ITERS"
echo "(the base model is downloaded from Hugging Face the first time, ~3.5 GB for 1.7B)"

mlx_lm.lora \
  --model "$BASE_MODEL" \
  --train \
  --data data \
  --iters "$ITERS" \
  --batch-size 2 \
  --num-layers 16 \
  --learning-rate 1e-5 \
  --mask-prompt \
  --grad-checkpoint \
  --steps-per-report 10 \
  --steps-per-eval 100 \
  --save-every 100 \
  --adapter-path adapters

echo
echo "Done. The trained part (adapter) is in training/adapters."
echo "Next step: bash 3_try.sh  — compare answers before and after training."
