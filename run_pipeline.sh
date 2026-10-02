#!/bin/bash

# Configuration
INPUT_FILE="MEQ Block1 2025-26 Qual Analysis - for AI - Colleges - sample 2 _completed - JB.xlsx"
OUTPUT_DIR="outputs"
FINAL_CSV="final_coded_results.csv"
REPORT_FILE="evaluation_report.txt"

# Column names in your Excel/CSV
# Adjust these based on your specific dataset
POS_COL="Comment1Positive"
IMP_COL="Comment1Improvement"

# Model selection
PROVIDER="google" # choices: openai, google
MODEL="gemini-1.5-flash"

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
uv run generate_codebooks.py \
  --input "$INPUT_FILE" \
  --output-dir "$OUTPUT_DIR" \
  --positive-column "$POS_COL" \
  --improvement-column "$IMP_COL" \
  --model "$MODEL" \
  --provider "$PROVIDER" \
  --batch-size "$GEN_BATCH_SIZE" \
  --overwrite

# Phase 2: Row-Level Coding
echo "--- Phase 2: Applying Codebooks ---"
uv run apply_codebooks.py \
  --input "$INPUT_FILE" \
  --positive-codebook "$OUTPUT_DIR/positive_codebook.md" \
  --improvement-codebook "$OUTPUT_DIR/improvement_codebook.md" \
  --output "$FINAL_CSV" \
  --positive-column "$POS_COL" \
  --improvement-column "$IMP_COL" \
  --model "$MODEL" \
  --provider "$PROVIDER" \
  --batch-size "$APPLY_BATCH_SIZE"

# Phase 3: Evaluation
echo "--- Phase 3: Evaluating Results ---"
uv run evaluate_results.py \
  --input "$FINAL_CSV" \
  --output "$REPORT_FILE"

echo "✅ Pipeline complete!"
echo "Codebooks: $OUTPUT_DIR/"
echo "Final results: $FINAL_CSV"
echo "Evaluation report: $REPORT_FILE"
