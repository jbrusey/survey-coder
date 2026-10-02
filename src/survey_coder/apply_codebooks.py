import os
import sys
import json
import time
import logging
import argparse
import re
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional
from openai import OpenAI
from tqdm import tqdm
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
    parser = argparse.ArgumentParser(description="Apply draft codebooks to code student feedback.")
    parser.add_argument("--input", required=True, help="Path to original .csv or .xlsx file")
    parser.add_argument("--positive-codebook", required=True, help="Path to positive_codebook.md")
    parser.add_argument("--improvement-codebook", required=True, help="Path to improvement_codebook.md")
    parser.add_argument("--output", required=True, help="Path to save final coded .csv")
    parser.add_argument("--positive-column", default="Comment1Positive", help="Name of the positive comments column")
    parser.add_argument("--improvement-column", default="Comment1Improvement", help="Name of the improvement comments column")
    parser.add_argument("--id-column", default=None, help="Name of the ID column (optional)")
    parser.add_argument("--model", default="gpt-4o-mini", help="LLM model name")
    parser.add_argument("--provider", default="openai", choices=["openai", "google"], help="LLM provider")
    parser.add_argument("--openai-base-url", default=None, help="Base URL for OpenAI-compatible API")
    parser.add_argument("--batch-size", type=int, default=5, help="Number of comments per coding batch")
    parser.add_argument("--temperature", type=float, default=0.0, help="LLM temperature (0.0 for deterministic coding)")
    parser.add_argument("--max-rows", type=int, default=None, help="Limit number of rows to process (for testing)")
    return parser.parse_args()

def load_data(path: str) -> pd.DataFrame:
    if path.endswith('.csv'):
        return pd.read_csv(path)
    elif path.endswith(('.xlsx', '.xls')):
        return pd.read_excel(path)
    else:
        raise ValueError("Unsupported file format.")

def extract_codes_from_md(md_path: str) -> List[str]:
    """Extracts code names from ## headers in the Markdown file."""
    with open(md_path, 'r') as f:
        content = f.read()
    
    # Matches "## 1. Code Name" or "## Code Name"
    # Group 1 is the code name
    codes = re.findall(r'^##\s+(?:\d+\.\s+)?(.+)$', content, re.MULTILINE)
    return [c.strip() for c in codes]

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
        api_key = os.environ.get("OPENAI_API_KEY", "dummy-key")
        client = OpenAI(api_key=api_key, base_url=base_url)
        
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

def parse_json_response(response_text: str) -> Dict:
    try:
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```"):
            lines = cleaned_text.splitlines()
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].startswith("```"): lines = lines[:-1]
            cleaned_text = "\n".join(lines).strip()
        return json.loads(cleaned_text)
    except:
        return {"error": "parse_failure", "raw": response_text}

def build_coding_prompt(stream_name: str, codebook_md: str, valid_codes: List[str], batch: List[Dict], comment_col: str, id_col: str) -> (str, str):
    system_prompt = (
        f"You are a qualitative research assistant. Your task is to apply a codebook to student {stream_name.lower()} feedback.\n\n"
        "### INSTRUCTIONS:\n"
        "1. Read the provided codebook carefully.\n"
        "2. For each comment, identify ALL applicable codes.\n"
        "3. Use ONLY the code names exactly as they appear in the codebook headers.\n"
        "4. If multiple codes apply, list them all.\n"
        "5. If a comment is too vague to code, use the 'Unclear / vague' code.\n"
        "6. If no specific codes apply but it is a valid comment, use the 'Other' code.\n"
        "7. Return the results in JSON format.\n\n"
        "### CODEBOOK:\n"
        f"{codebook_md}"
    )
    
    formatted_comments = ""
    for r in batch:
        comment_text = str(r.get(comment_col, "")).replace("\n", " ")
        formatted_comments += f"ID: {r[id_col]}\nComment: {comment_text}\n\n"

    user_prompt = (
        f"Code the following {len(batch)} student comments using the provided codebook.\n\n"
        f"{formatted_comments}"
        "Return JSON with this structure:\n"
        "{\n"
        "  \"results\": [\n"
        "    { \"id\": \"...\", \"codes\": [\"Code A\", \"Code B\"] },\n"
        "    ...\n"
        "  ]\n"
        "}\n"
    )
    return system_prompt, user_prompt

def process_stream(df: pd.DataFrame, stream_name: str, md_path: str, comment_col: str, id_col: str, args: argparse.Namespace) -> Dict[str, List[str]]:
    valid_codes = extract_codes_from_md(md_path)
    with open(md_path, 'r') as f:
        codebook_md = f.read()
    
    # Filter rows that have a comment
    mask = df[comment_col].notna() & (df[comment_col].astype(str).str.strip().str.len() > 0)
    rows_to_process = df[mask].to_dict('records')
    
    if args.max_rows:
        rows_to_process = rows_to_process[:args.max_rows]
    
    results_map = {} # id -> list of codes
    
    batches = [rows_to_process[i:i + args.batch_size] for i in range(0, len(rows_to_process), args.batch_size)]
    
    logger.info(f"Coding {len(rows_to_process)} {stream_name} comments in {len(batches)} batches.")
    
    for batch in tqdm(batches, desc=f"Coding {stream_name}"):
        sys_p, user_p = build_coding_prompt(stream_name, codebook_md, valid_codes, batch, comment_col, id_col)
        
        try:
            response_text = call_llm(user_p, sys_p, args.model, args.temperature, args.openai_base_url, args.provider)
            batch_results = parse_json_response(response_text)
            
            if "results" in batch_results:
                for res in batch_results["results"]:
                    # Basic validation: ensure codes are in the codebook
                    clean_codes = [c for c in res.get("codes", []) if c in valid_codes]
                    # If empty but the LLM tried to say something, maybe it missed the exact name? 
                    # For now, we stay strict.
                    results_map[str(res["id"])] = clean_codes
            else:
                logger.warning(f"Batch response missing 'results' key: {batch_results}")
        except Exception as e:
            logger.error(f"Failed to process batch: {e}")
            continue
            
    return results_map, valid_codes

def main():
    args = parse_args()
    
    df = load_data(args.input)
    
    # Handle ID column
    id_col = args.id_column
    if not id_col or id_col not in df.columns:
        id_col = "generated_id"
        df[id_col] = [f"R{i+1:06d}" for i in range(len(df))]
    
    df[id_col] = df[id_col].astype(str)
    
    # Process Positive
    pos_results, pos_codes = process_stream(df, "Positive", args.positive_codebook, args.positive_column, id_col, args)
    
    # Process Improvement
    imp_results, imp_codes = process_stream(df, "Improvement", args.improvement_codebook, args.improvement_column, id_col, args)
    
    logger.info("Collating results...")
    
    # Create new columns for Positive codes
    for code in pos_codes:
        col_name = f"Pos_{code}"
        df[col_name] = df[id_col].apply(lambda x: 1 if code in pos_results.get(x, []) else 0)
    
    # Create new columns for Improvement codes
    for code in imp_codes:
        col_name = f"Imp_{code}"
        df[col_name] = df[id_col].apply(lambda x: 1 if code in imp_results.get(x, []) else 0)
    
    # Summary of coding
    df['Pos_Codes_Applied'] = df[id_col].apply(lambda x: ", ".join(pos_results.get(x, [])))
    df['Imp_Codes_Applied'] = df[id_col].apply(lambda x: ", ".join(imp_results.get(x, [])))
    
    df.to_csv(args.output, index=False)
    logger.info(f"Final coded dataset saved to {args.output}")

if __name__ == "__main__":
    main()
