#!/bin/bash

# Configuration
INPUT_FILE="MEQ Block1 2025-26 Qual Analysis - for AI - Colleges - sample 2 _completed - JB.xlsx"
OUTPUT_DIR="outputs"
FINAL_CSV="final_coded_results.csv"
REPORT_FILE="evaluation_report.txt"

CONFIG="survey_config.example.json"

# OpenAI-compatible vLLM server
VLLM_HOST="${VLLM_HOST:-100.101.42.121}"
VLLM_BASE_URL="${VLLM_BASE_URL:-http://${VLLM_HOST}:8000/v1}"
: "${VLLM_API_KEY:?Set VLLM_API_KEY before running this script}"
PROVIDER="openai"
MODEL="Qwen/Qwen3.5-9B"

# Batch settings
GEN_BATCH_SIZE=100
APPLY_BATCH_SIZE=10

# Exit on error
set -e

echo "🚀 Starting Qualitative Feedback Analysis Pipeline..."

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Phase 1: Theme Discovery
echo "--- Phase 1: Generating Codebooks ---"
uv run generate-codebooks \
  --input "$INPUT_FILE" \
  --output-dir "$OUTPUT_DIR" \
  --config "$CONFIG" \
  --model "$MODEL" \
  --provider "$PROVIDER" \
  --openai-base-url "$VLLM_BASE_URL" \
  --batch-size "$GEN_BATCH_SIZE" \
  --overwrite

# Phase 2: Row-Level Coding
echo "--- Phase 2: Applying Codebooks ---"
uv run apply-codebooks \
  --input "$INPUT_FILE" \
  --config "$CONFIG" \
  --output "$FINAL_CSV" \
  --model "$MODEL" \
  --provider "$PROVIDER" \
  --openai-base-url "$VLLM_BASE_URL" \
  --batch-size "$APPLY_BATCH_SIZE"

# Phase 3: Evaluation
echo "--- Phase 3: Evaluating Results ---"
uv run evaluate-results \
  --input "$FINAL_CSV" \
  --output "$REPORT_FILE"

echo "✅ Pipeline complete!"
echo "Codebooks: $OUTPUT_DIR/"
echo "Final results: $FINAL_CSV"
echo "Evaluation report: $REPORT_FILE"
