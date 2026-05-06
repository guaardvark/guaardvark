"""CLI entry: python -m backend.services.system_mapper <root> [--out DIR]."""
import argparse
import sys
from pathlib import Path

from .core import codebase_map
from .exporters import export_all


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.services.system_mapper",
        description="Generate a SystemMap (deps + reachability + tool graph + findings).",
    )
    parser.add_argument("root", help="Path to the codebase root")
    parser.add_argument("--out", default=None, help="Output directory (default: <root>/system_map_<ts>)")
    parser.add_argument("--exclude", action="append", default=[],
                        help="Additional directory names to exclude (repeatable)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    print(f"Mapping {root} …", file=sys.stderr)
    smap = codebase_map(root, frozenset(args.exclude))

    out_dir = Path(args.out) if args.out else root / f"system_map_{int(smap.generated_at)}"
    paths = export_all(smap, out_dir)

    print(f"  files surveyed:    {smap.file_count}", file=sys.stderr)
    print(f"  findings:          {len(smap.findings)}", file=sys.stderr)
    counts: dict[str, int] = {}
    for f in smap.findings:
        counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
    for sev in ("high", "medium", "low", "info"):
        if counts.get(sev):
            print(f"    {sev:6}  {counts[sev]}", file=sys.stderr)
    print(f"\nWrote:", file=sys.stderr)
    for k, v in paths.items():
        print(f"  {k:9}  {v}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
