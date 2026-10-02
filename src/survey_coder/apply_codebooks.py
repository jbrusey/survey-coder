import os
import sys
import json
import time
import logging
import argparse
import re
import pandas as pd
from typing import List, Dict, Any, Optional
from openai import OpenAI
from tqdm import tqdm
import google.generativeai as genai
from dotenv import load_dotenv
from .data import load_data
from .config import load_config, load_prompt

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
    parser.add_argument("--config", help="JSON config containing context and a streams list")
    parser.add_argument("--prompt-dir", default="prompts", help="Directory containing prompt templates")
    parser.add_argument("--output", required=True, help="Path to save final coded .csv")
    parser.add_argument("--id-column", default=None, help="Name of the ID column (optional)")
    parser.add_argument("--model", default="gpt-4o-mini", help="LLM model name")
    parser.add_argument("--provider", default="openai", choices=["openai", "google"], help="LLM provider")
    parser.add_argument("--openai-base-url", default=None, help="Base URL for OpenAI-compatible API")
    parser.add_argument("--batch-size", type=int, default=5, help="Number of comments per coding batch")
    parser.add_argument("--temperature", type=float, default=0.0, help="LLM temperature (0.0 for deterministic coding)")
    parser.add_argument("--max-rows", type=int, default=None, help="Limit number of rows to process (for testing)")
    return parser.parse_args()

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
        api_key = os.environ.get("VLLM_API_KEY") if base_url else os.environ.get("OPENAI_API_KEY")
        client = OpenAI(
            api_key=api_key or "dummy-key",
            base_url=base_url,
            timeout=120 if base_url else 600,
            max_retries=0 if base_url else 2,
        )
        
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": 1024,
        }
        if base_url and model.startswith("Qwen/"):
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
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

def build_coding_prompt(stream: Dict, context: str, codebook_md: str, batch: List[Dict], id_col: str, prompts: Dict[str, str]) -> (str, str):
    comments = "".join(f"ID: {r[id_col]}\nComment: {str(r.get(stream['column'], '')).replace(chr(10), ' ')}\n\n" for r in batch)
    values = {"context": context, "stream_name": stream["name"], "codebook": codebook_md, "count": len(batch), "comments": comments}
    return prompts["system"].format(**values), prompts["batch"].format(**values)

def process_stream(df: pd.DataFrame, stream: Dict, id_col: str, args: argparse.Namespace, context: str, prompts: Dict[str, str]) -> Dict[str, List[str]]:
    stream_name, md_path, comment_col = stream["name"], stream["codebook"], stream["column"]
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
        sys_p, user_p = build_coding_prompt(stream, context, codebook_md, batch, id_col, prompts)
        
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
    
    config = load_config(args.config)
    prompts = {"system": load_prompt(args.prompt_dir, "code_system.txt"), "batch": load_prompt(args.prompt_dir, "code_batch.txt")}
    results = []
    for stream in config["streams"]:
        stream.setdefault("codebook", f"{stream['name'].lower()}_codebook.md")
        result, codes = process_stream(df, stream, id_col, args, config["context"], prompts)
        results.append((stream, result, codes))

    logger.info("Collating results...")
    for stream, result_map, codes in results:
        prefix = stream.get("prefix", stream["name"])
        for code in codes:
            df[f"{prefix}_{code}"] = df[id_col].apply(lambda x, c=code: 1 if c in result_map.get(x, []) else 0)
        df[f"{prefix}_Codes_Applied"] = df[id_col].apply(lambda x: ", ".join(result_map.get(x, [])))
    
    df.to_csv(args.output, index=False)
    logger.info(f"Final coded dataset saved to {args.output}")

if __name__ == "__main__":
    main()
