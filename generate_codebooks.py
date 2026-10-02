import os
import sys
import json
import time
import random
import logging
import argparse
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional
from openai import OpenAI
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from home directory then local directory
load_dotenv(os.path.expanduser("~/.env"))
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser(description="Generate draft qualitative codebooks from student feedback.")
    parser.add_argument("--input", required=True, help="Path to input .csv or .xlsx file")
    parser.add_argument("--output-dir", required=True, help="Directory to save outputs")
    parser.add_argument("--positive-column", default="Positive", help="Name of the positive comments column")
    parser.add_argument("--improvement-column", default="Improvement", help="Name of the improvement comments column")
    parser.add_argument("--id-column", default=None, help="Name of the ID column (optional)")
    parser.add_argument("--model", default="gpt-4o-mini", help="LLM model name")
    parser.add_argument("--provider", default="openai", choices=["openai", "google"], help="LLM provider")
    parser.add_argument("--openai-base-url", default=None, help="Base URL for OpenAI-compatible API (e.g., http://localhost:11434/v1 for Ollama)")
    parser.add_argument("--batch-size", type=int, default=100, help="Number of comments per batch")
    parser.add_argument("--sample-size", type=int, default=1000, help="Max comments to sample per stream")
    parser.add_argument("--random-seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--min-length", type=int, default=5, help="Minimum comment length")
    parser.add_argument("--max-themes-per-batch", type=int, default=20, help="Max themes the LLM should identify per batch")
    parser.add_argument("--max-final-codes", type=int, default=30, help="Max codes in the final consolidated codebook")
    parser.add_argument("--temperature", type=float, default=0.2, help="LLM temperature")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output directory")
    return parser.parse_args()

def load_feedback(path: str) -> pd.DataFrame:
    logger.info(f"Loading data from {path}")
    if path.endswith('.csv'):
        return pd.read_csv(path)
    elif path.endswith(('.xlsx', '.xls')):
        return pd.read_excel(path)
    else:
        raise ValueError("Unsupported file format. Use .csv or .xlsx")

def validate_columns(df: pd.DataFrame, positive_col: str, improvement_col: str):
    missing = []
    if positive_col not in df.columns:
        missing.append(positive_col)
    if improvement_col not in df.columns:
        missing.append(improvement_col)
    if missing:
        raise ValueError(f"Required columns missing: {', '.join(missing)}")

def ensure_id_column(df: pd.DataFrame, id_col: Optional[str]) -> (pd.DataFrame, str):
    if id_col and id_col in df.columns:
        return df, id_col
    
    logger.info("ID column not found or not supplied. Creating stable IDs.")
    new_id_col = "generated_id"
    df[new_id_col] = [f"R{i+1:06d}" for i in range(len(df))]
    return df, new_id_col

def clean_comment(text: Any, min_length: int) -> Optional[str]:
    if pd.isna(text):
        return None
    text = str(text).strip()
    # Common placeholders for empty/useless responses
    low_value = {"nan", "none", "n/a", ".", "-", "nil", "nothing", "no", "none."}
    if text.lower() in low_value:
        return None
    if len(text) < min_length:
        return None
    return text

def extract_stream(df: pd.DataFrame, stream_name: str, comment_col: str, id_col: str, min_length: int) -> pd.DataFrame:
    logger.info(f"Extracting {stream_name} stream from column '{comment_col}'")
    cols_to_keep = [id_col, comment_col]
    for meta in ["lecture", "session", "week", "module", "College", "School", "Level"]:
        if meta in df.columns:
            cols_to_keep.append(meta)
    
    stream_df = df[cols_to_keep].copy()
    stream_df['cleaned_comment'] = stream_df[comment_col].apply(lambda x: clean_comment(x, min_length))
    stream_df = stream_df.dropna(subset=['cleaned_comment'])
    return stream_df

def sample_comments(df: pd.DataFrame, sample_size: int, seed: int) -> pd.DataFrame:
    if len(df) <= sample_size:
        return df
    
    logger.info(f"Sampling {sample_size} comments from {len(df)} total")
    stratify_col = next((c for c in ["lecture", "session", "module", "School"] if c in df.columns), None)
    
    if stratify_col:
        return df.groupby(stratify_col, group_keys=False).apply(
            lambda x: x.sample(min(len(x), int(len(x)/len(df) * sample_size)), random_state=seed)
        ).sample(frac=1, random_state=seed).head(sample_size)
    else:
        return df.sample(n=sample_size, random_state=seed)

def make_batches(records: List[Dict], batch_size: int) -> List[List[Dict]]:
    return [records[i:i + batch_size] for i in range(0, len(records), batch_size)]

def call_llm(prompt: str, system_prompt: str, model: str, temperature: float, base_url: Optional[str] = None, provider: str = "openai") -> str:
    if provider == "google":
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable not set.")
        genai.configure(api_key=api_key)
        
        combined_prompt = f"{system_prompt}\n\n{prompt}"
        
        gemini_model = genai.GenerativeModel(model)
        generation_config = genai.GenerationConfig(
            temperature=temperature,
            response_mime_type="application/json"
        )
        
        response = gemini_model.generate_content(
            combined_prompt,
            generation_config=generation_config
        )
        return response.text
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "dummy-key-for-local-llm")
        client = OpenAI(api_key=api_key, base_url=base_url)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                kwargs = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                }
                if not base_url or "ollama" in base_url.lower():
                    kwargs["response_format"] = {"type": "json_object"}
                
                response = client.chat.completions.create(**kwargs)
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

def parse_json_response(response_text: str) -> Dict:
    try:
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```"):
            lines = cleaned_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned_text = "\n".join(lines).strip()
            
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        return {"error": "JSON parse failure", "raw": response_text}

def build_batch_prompt(stream_name: str, records: List[Dict], max_themes: int, comment_col: str, id_col: str) -> (str, str):
    question = "What was positive about the lecture/session?" if stream_name == "Positive" else "What could be improved about the lecture/session?"
    
    system_prompt = (
        f"You are assisting with qualitative analysis of student lecture feedback. Your task is to identify recurring themes in {stream_name.lower()} comments. "
        "You are not coding individual comments yet. You are producing candidate themes for a later human-reviewed codebook."
    )
    
    formatted_comments = ""
    for r in records:
        meta = f" [{r.get('lecture', '')}]" if 'lecture' in r else ""
        formatted_comments += f"ID: {r[id_col]}{meta}\nComment: {r['cleaned_comment']}\n\n"

    user_prompt = (
        f"Below is a batch of student comments responding to the question: \"{question}\"\n\n"
        "Identify recurring themes.\n\n"
        "Rules:\n"
        "- Focus on repeated ideas, not one-off remarks.\n"
        "- Do not invent themes that are not supported by comments.\n"
        "- Keep code names short and clear.\n"
        "- Distinguish genuinely different themes.\n"
        "- Merge near-duplicates.\n"
        "- Include example comment IDs and short evidence phrases.\n"
        "- Return valid JSON only.\n\n"
        f"Limit the result to at most {max_themes} themes.\n\n"
        "Return JSON with this structure:\n"
        "{\n"
        "  \"themes\": [\n"
        "    {\n"
        "      \"code_name\": \"...\",\n"
        "      \"definition\": \"...\",\n"
        "      \"include_when\": [\"...\"],\n"
        "      \"exclude_when\": [\"...\"],\n"
        "      \"indicators\": [\"...\"],\n"
        "      \"example_comment_ids\": [\"...\"],\n"
        "      \"example_evidence\": [\"...\"]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Comments:\n"
        f"{formatted_comments}"
    )
    return system_prompt, user_prompt

def build_consolidation_prompt(stream_name: str, batch_themes: List[Dict], max_final_codes: int) -> (str, str):
    system_prompt = (
        "You are a qualitative research expert. You are consolidating multiple batches of candidate themes into a single, high-quality draft codebook. "
        "The goal is to create a codebook that a human can use to reliably code student feedback comments."
    )
    
    themes_data = json.dumps(batch_themes, indent=2)
    
    user_prompt = (
        f"Below are candidate themes identified from multiple batches of student {stream_name.lower()} feedback.\n\n"
        "Consolidate these into a final draft codebook.\n\n"
        "Consolidation rules:\n"
        "- Merge duplicate or near-duplicate themes.\n"
        "- Preserve important distinctions.\n"
        "- Remove weak themes with little support (only 1-2 examples across all batches).\n"
        f"- Produce no more than {max_final_codes} codes.\n"
        "- Include an \"Unclear / vague\" code.\n"
        "- Include an \"Other\" code.\n"
        "- Ensure codes are suitable for later row-level multi-label coding.\n"
        "- Produce clear include/exclude rules.\n"
        "- Produce example evidence phrases.\n\n"
        "Return JSON with this structure:\n"
        "{\n"
        f"  \"codebook_name\": \"{stream_name} feedback codebook\",\n"
        f"  \"stream\": \"{stream_name}\",\n"
        "  \"codes\": [\n"
        "    {\n"
        "      \"code\": \"...\",\n"
        "      \"definition\": \"...\",\n"
        "      \"include_when\": [\"...\"],\n"
        "      \"exclude_when\": [\"...\"],\n"
        "      \"indicators\": [\"...\"],\n"
        "      \"example_evidence\": [\"...\"],\n"
        "      \"notes\": \"...\"\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"Candidate Themes Data:\n{themes_data}"
    )
    return system_prompt, user_prompt

def save_markdown_codebook(path: str, codebook: Dict):
    with open(path, 'w') as f:
        f.write(f"# {codebook['codebook_name']}\n\n")
        f.write(f"Stream: {codebook['stream']}\n\n")
        
        for i, code in enumerate(codebook['codes'], 1):
            f.write(f"## {i}. {code['code']}\n\n")
            f.write(f"**Definition:**\n{code['definition']}\n\n")
            
            f.write("**Include when:**\n")
            for item in code.get('include_when', []):
                f.write(f"- {item}\n")
            f.write("\n")
            
            f.write("**Exclude when:**\n")
            for item in code.get('exclude_when', []):
                f.write(f"- {item}\n")
            f.write("\n")
            
            f.write("**Indicators:**\n")
            for item in code.get('indicators', []):
                f.write(f"- {item}\n")
            f.write("\n")
            
            f.write("**Example evidence:**\n")
            for item in code.get('example_evidence', []):
                f.write(f"- \"{item}\"\n")
            f.write("\n")
            
            if code.get('notes'):
                f.write(f"**Notes:**\n{code['notes']}\n\n")
            
            f.write("---\n\n")

def process_stream(stream_name: str, df: pd.DataFrame, args: argparse.Namespace, id_col: str, comment_col: str):
    logger.info(f"--- Processing {stream_name} Stream ---")
    
    batch_out_dir = os.path.join(args.output_dir, "batch_outputs")
    os.makedirs(batch_out_dir, exist_ok=True)
    
    records = df.to_dict('records')
    batches = make_batches(records, args.batch_size)
    
    all_batch_themes = []
    
    for i, batch in enumerate(batches):
        batch_num = i + 1
        logger.info(f"Processing {stream_name} batch {batch_num}/{len(batches)} ({len(batch)} comments)")
        
        sys_p, user_p = build_batch_prompt(stream_name, batch, args.max_themes_per_batch, comment_col, id_col)
        
        try:
            response_text = call_llm(user_p, sys_p, args.model, args.temperature, args.openai_base_url, args.provider)
            batch_data = parse_json_response(response_text)
            
            batch_data['metadata'] = {
                "stream": stream_name,
                "batch_number": batch_num,
                "model": args.model,
                "timestamp": datetime.now().isoformat(),
                "num_comments": len(batch)
            }
            
            batch_filename = f"{stream_name.lower()}_batch_{batch_num:03d}.json"
            with open(os.path.join(batch_out_dir, batch_filename), 'w') as f:
                json.dump(batch_data, f, indent=2)
            
            if "themes" in batch_data:
                all_batch_themes.extend(batch_data['themes'])
                
        except Exception as e:
            logger.error(f"Failed to process batch {batch_num}: {e}")
            continue

    if not all_batch_themes:
        logger.error(f"No themes extracted for {stream_name} stream.")
        return None

    logger.info(f"Consolidating {len(all_batch_themes)} candidate themes into final {stream_name} codebook")
    sys_p_con, user_p_con = build_consolidation_prompt(stream_name, all_batch_themes, args.max_final_codes)
    
    try:
        con_response_text = call_llm(user_p_con, sys_p_con, args.model, args.temperature, args.openai_base_url, args.provider)
        final_codebook = parse_json_response(con_response_text)
        
        md_path = os.path.join(args.output_dir, f"{stream_name.lower()}_codebook.md")
        save_markdown_codebook(md_path, final_codebook)

        return final_codebook
    except Exception as e:
        logger.error(f"Failed to consolidate codebook for {stream_name}: {e}")
        return None

def main():
    args = parse_args()
    
    if os.path.exists(args.output_dir) and not args.overwrite:
        logger.error(f"Output directory {args.output_dir} already exists. Use --overwrite to replace.")
        sys.exit(1)
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Validate API keys based on provider
    if args.provider == "google":
        if not os.environ.get("GOOGLE_API_KEY"):
            logger.error("GOOGLE_API_KEY environment variable not set.")
            sys.exit(1)
    elif args.provider == "openai":
        if not os.environ.get("OPENAI_API_KEY") and not args.openai_base_url:
            logger.error("Neither OPENAI_API_KEY environment variable nor --openai-base-url supplied.")
            sys.exit(1)

    try:
        df = load_feedback(args.input)
        validate_columns(df, args.positive_column, args.improvement_column)
        df, id_col = ensure_id_column(df, args.id_column)
        
        pos_df = extract_stream(df, "Positive", args.positive_column, id_col, args.min_length)
        imp_df = extract_stream(df, "Improvement", args.improvement_column, id_col, args.min_length)
        
        pos_sampled = sample_comments(pos_df, args.sample_size, args.random_seed)
        imp_sampled = sample_comments(imp_df, args.sample_size, args.random_seed)
        
        pos_codebook = process_stream("Positive", pos_sampled, args, id_col, args.positive_column)
        imp_codebook = process_stream("Improvement", imp_sampled, args, id_col, args.improvement_column)
        
        summary = {
            "timestamp": datetime.now().isoformat(),
            "input_file": args.input,
            "total_rows_loaded": len(df),
            "usable_positive_comments": len(pos_df),
            "usable_improvement_comments": len(imp_df),
            "sample_size_per_stream": args.sample_size,
            "batch_size": args.batch_size,
            "model": args.model,
            "provider": args.provider,
            "outputs_produced": [
                f"{args.output_dir}/positive_codebook.md",
                f"{args.output_dir}/improvement_codebook.md",
                f"{args.output_dir}/run_summary.json",
                f"{args.output_dir}/batch_outputs/ (raw discovery JSON)"
            ]
        }
        
        with open(os.path.join(args.output_dir, "run_summary.json"), 'w') as f:
            json.dump(summary, f, indent=2)
            
        logger.info("Pipeline complete. Codebooks generated successfully.")
        
    except Exception as e:
        logger.exception(f"An error occurred during the pipeline: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
