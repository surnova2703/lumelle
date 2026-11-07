#!/usr/bin/env bash
# Step 4: turn your trained model into an Ollama model, so it appears in Lumelle's model list.
# Needs ~10 GB free disk for a while (step 5 cleans up) and the internet once (converter tools).
. "$(dirname "$0")/common.sh"
need_mac
[ -d adapters ] || { echo "No trained adapter yet: run bash 2_train.sh first"; exit 1; }
command -v ollama >/dev/null || { echo "Ollama is not found. Start the Ollama app first."; exit 1; }
activate

echo "1/4  Merging the adapter into the model…"
mlx_lm.fuse --model "$BASE_MODEL" --adapter-path adapters --save-path fused

echo "2/4  Getting the converter (llama.cpp)…"
[ -d llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp
python -m pip install --quiet --default-timeout=120 --retries 10 -r llama.cpp/requirements/requirements-convert_hf_to_gguf.txt

echo "3/4  Converting to GGUF (the format Ollama uses)…"
python llama.cpp/convert_hf_to_gguf.py fused --outtype f16 --outfile "$NAME-f16.gguf"

echo "4/4  Creating the Ollama model '$NAME' (compressed to q4_K_M, ~1.1 GB for 1.7B)…"
cat > Modelfile <<MODELFILE
FROM ./$NAME-f16.gguf
PARAMETER temperature 0.4
MODELFILE
ollama create "$NAME" --quantize q4_K_M -f Modelfile

echo
ollama run "$NAME" "Привет! Кто ты? Ответь одним предложением."
echo
echo "Ready. In Lumelle (signed in as admin) choose '$NAME:latest' in the Model list at the bottom."
echo "If you are happy with it, free the disk: bash 5_cleanup.sh"
