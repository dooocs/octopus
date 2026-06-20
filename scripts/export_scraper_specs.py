from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.registry import export_specs


DEFAULT_OUTPUT = Path("web/src/generated/scraper_specs.json")


def export_scraper_specs(output: Path = DEFAULT_OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(export_specs(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Octopus scraper ChannelSpec JSON")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    path = export_scraper_specs(Path(args.output))
    print(f"exported scraper specs: {path}")


if __name__ == "__main__":
    main()
