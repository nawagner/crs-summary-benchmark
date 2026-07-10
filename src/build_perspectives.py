"""Compile + validate perspectives.yaml into docs/data/perspectives.json (issue #9).

perspectives.yaml is a hand-curated file of VERBATIM sponsor-vs-opponent framing quotes
for bills (see that file's header for the full schema and editorial rules). This script
just validates and compiles it — it never generates or rewrites quote text.

Validation collects every error (not just the first) and prints each with a dotted path
to the offending value (e.g. `bills.118-hr-2670.sponsor[0].source_url: not http(s)`),
then exits(1) if anything failed. The compiled artifact writes `generated_at: null` when
`bills` is empty, so the shipped-empty artifact (no curated content yet) stays byte-for-byte
reproducible run to run.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

BILL_ID_RE = re.compile(r"^\d+-[a-z]+-\d+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STANCES = {"sponsor", "supporter", "opponent"}
SOURCE_KINDS = {"press_release", "floor_statement", "dear_colleague", "op_ed"}
GROUP_KEYS = {"sponsor", "opponents"}
# which `stance` values are allowed for entries listed under each group key
GROUP_STANCES = {"sponsor": {"sponsor", "supporter"}, "opponents": {"opponent"}}

ENTRY_REQUIRED_KEYS = {"name", "stance", "excerpt", "source_url", "source_kind"}
ENTRY_OPTIONAL_KEYS = {"date"}
ENTRY_ALLOWED_KEYS = ENTRY_REQUIRED_KEYS | ENTRY_OPTIONAL_KEYS

MAX_EXCERPT_CHARS = 600


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _validate_entry(entry: object, prefix: str, allowed_stances: set[str]) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{prefix}: must be a mapping"]

    errors = []
    for key in sorted(set(entry.keys()) - ENTRY_ALLOWED_KEYS):
        errors.append(f"{prefix}.{key}: unknown key")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{prefix}.name: required nonempty string")

    stance = entry.get("stance")
    if stance not in STANCES:
        errors.append(f"{prefix}.stance: must be one of {sorted(STANCES)}")
    elif stance not in allowed_stances:
        errors.append(
            f"{prefix}.stance: '{stance}' not allowed here (expected one of {sorted(allowed_stances)})"
        )

    excerpt = entry.get("excerpt")
    if not isinstance(excerpt, str) or not excerpt.strip():
        errors.append(f"{prefix}.excerpt: required nonempty string")
    elif len(excerpt) > MAX_EXCERPT_CHARS:
        errors.append(f"{prefix}.excerpt: exceeds {MAX_EXCERPT_CHARS} chars (got {len(excerpt)})")

    source_url = entry.get("source_url")
    if not isinstance(source_url, str) or not _is_http_url(source_url):
        errors.append(f"{prefix}.source_url: not http(s)")

    source_kind = entry.get("source_kind")
    if source_kind not in SOURCE_KINDS:
        errors.append(f"{prefix}.source_kind: must be one of {sorted(SOURCE_KINDS)}")

    if "date" in entry:
        date = entry["date"]
        if not isinstance(date, str) or not DATE_RE.match(date):
            errors.append(f"{prefix}.date: must match YYYY-MM-DD")

    return errors


def validate(data: dict) -> list[str]:
    """Validate the parsed perspectives.yaml structure. Returns a list of error strings
    (each prefixed with a dotted path to the offending value); empty means valid. Collects
    every error found rather than stopping at the first."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["root: must be a mapping"]

    bills = data.get("bills")
    if not isinstance(bills, dict):
        return ["bills: must be a mapping"]

    for bill_id, bill_val in bills.items():
        prefix = f"bills.{bill_id}"
        if not isinstance(bill_id, str) or not BILL_ID_RE.match(bill_id):
            errors.append(f"{prefix}: key does not match ^\\d+-[a-z]+-\\d+$")

        if not isinstance(bill_val, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue

        for key in sorted(set(bill_val.keys()) - GROUP_KEYS):
            errors.append(f"{prefix}.{key}: unknown key (expected sponsor|opponents)")

        for group in ("sponsor", "opponents"):
            if group not in bill_val:
                continue
            entries = bill_val[group]
            group_prefix = f"{prefix}.{group}"
            if not isinstance(entries, list) or not entries:
                errors.append(f"{group_prefix}: must be a non-empty list")
                continue
            for i, entry in enumerate(entries):
                errors.extend(_validate_entry(entry, f"{group_prefix}[{i}]", GROUP_STANCES[group]))

    return errors


def main() -> None:
    yaml_path = C.ROOT / "perspectives.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    errors = validate(data)
    if errors:
        print(f"{len(errors)} error(s) in {yaml_path}:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    bills = data["bills"]
    out = {
        # null (not a timestamp) whenever bills is empty, so the artifact is reproducible
        # byte-for-byte even before any curation has happened.
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()) if bills else None,
        "bills": bills,
    }
    out_path = C.DOCS_DATA / "perspectives.json"
    C.write_json(out_path, out)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
