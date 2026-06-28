"""Shared helpers: env/config loading, OpenRouter client, HTML→text, paths."""
from __future__ import annotations

import html
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_BILLS = ROOT / "data" / "bills"
RESULTS = ROOT / "results"
SUMMARIES_DIR = RESULTS / "summaries"
SCORES_DIR = RESULTS / "scores"
DOCS_DATA = ROOT / "docs" / "data"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CRS_REFERENCE = "crs_reference"  # pseudo-model id for the human-authored CRS summary


# --------------------------------------------------------------------------- env
def load_env() -> None:
    """Load .env and tolerate the OPENROUTER_APY_KEY typo seen in the wild."""
    load_dotenv(ROOT / ".env")
    if not os.environ.get("OPENROUTER_API_KEY") and os.environ.get("OPENROUTER_APY_KEY"):
        os.environ["OPENROUTER_API_KEY"] = os.environ["OPENROUTER_APY_KEY"]


def require_openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit(
            "OPENROUTER_API_KEY not set. Add it to .env "
            "(see https://openrouter.ai/keys)."
        )
    return key


def require_congress_key() -> str:
    key = os.environ.get("CONGRESS_API_KEY")
    if not key:
        raise SystemExit(
            "CONGRESS_API_KEY not set. Add it to .env "
            "(free key: https://api.congress.gov/sign-up/)."
        )
    return key


# ------------------------------------------------------------------------- config
def load_config() -> dict[str, Any]:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_criteria() -> list[dict[str, Any]]:
    with open(ROOT / "criteria.yaml") as f:
        return yaml.safe_load(f)["criteria"]


def read_prompt(rel_path: str) -> str:
    with open(ROOT / rel_path) as f:
        return f.read()


# ------------------------------------------------------------------- openrouter
def openrouter_client():
    """An OpenAI-SDK client pointed at OpenRouter."""
    from openai import OpenAI

    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=require_openrouter_key(),
        default_headers={
            "HTTP-Referer": "https://github.com/nawagner/crs-summary-benchmark",
            "X-Title": "CRS Summary Benchmark",
        },
    )


# ----------------------------------------------------------------------- helpers
class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    """Strip HTML/CDATA to readable plain text, collapsing excess whitespace."""
    if not raw:
        return ""
    raw = re.sub(r"<!\[CDATA\[|\]\]>", "", raw)
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token)."""
    return len(text) // 4


def model_slug(model_id: str) -> str:
    """Filesystem-safe directory name for an OpenRouter model id."""
    return model_id.replace("/", "__").replace(":", "_")


def bill_id(congress: int | str, bill_type: str, number: int | str) -> str:
    return f"{congress}-{str(bill_type).lower()}-{number}"


def read_json(path: Path) -> Any:
    with open(path) as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def list_bill_files() -> list[Path]:
    return sorted(DATA_BILLS.glob("*.json"))
