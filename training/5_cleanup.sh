#!/usr/bin/env bash
# Step 5: delete the large temporary files of training. Your model stays in Ollama,
# and the adapter + data stay in training/ so you can train again later.
. "$(dirname "$0")/common.sh"
du -sh fused llama.cpp "$VENV" ./*.gguf 2>/dev/null || true
read -r -p "Delete these temporary files? [y/N] " a
if [ "$a" = "y" ] || [ "$a" = "Y" ]; then
  rm -rf fused llama.cpp ./*.gguf Modelfile
  echo "Deleted. To also remove the training tools: rm -rf training/$VENV"
  echo "The base model downloaded from Hugging Face is in ~/.cache/huggingface (can be deleted too)."
fi
echo
echo "When your model works well, you can remove the old one to free ~5 GB:  ollama rm qwen3:8b"
echo "(keep it if you still want it as the teacher or as a fallback)"
