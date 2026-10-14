"""Tools for LLM-assisted qualitative survey coding."""

from pathlib import Path

from dotenv import load_dotenv
import truststore

# Keep secrets out of setup files and load them before CLI/GUI entry points read
# the provider environment. Existing shell variables take precedence.
load_dotenv(Path.home() / ".env")
load_dotenv(Path.cwd() / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

truststore.inject_into_ssl()
