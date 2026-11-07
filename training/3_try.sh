#!/usr/bin/env bash
# Step 3: compare the base model and your trained model on the same question.
#   bash 3_try.sh "Объясни Present Perfect и дай упражнение"
. "$(dirname "$0")/common.sh"
need_mac
activate
Q="${1:-Что такое VPN? Объясни простыми словами}"
SYSTEM=$(cd .. && .venv/bin/python -c "from main import system_prompt; print(system_prompt('chat', 'ru'))" 2>/dev/null || echo "You are Lumelle, a helpful assistant.")
echo "=== Base model ($BASE_MODEL) ==="
mlx_lm.generate --model "$BASE_MODEL" --system-prompt "$SYSTEM" --prompt "$Q" --max-tokens 400 --temp 0.4
echo
echo "=== Your model (after training) ==="
mlx_lm.generate --model "$BASE_MODEL" --adapter-path adapters --system-prompt "$SYSTEM" --prompt "$Q" --max-tokens 400 --temp 0.4
echo
echo "If you like the second answer: bash 4_export.sh"
