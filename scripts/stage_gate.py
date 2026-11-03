"""Product-specific release criteria, in addition to the toolkit's six checks."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT):
    from evaluate import load_suite

    manifest_path = root / "data/evaluation/manifest.json"
    try:
        manifest, cases = load_suite(manifest_path)
        output = root / "data/evaluation/runs"
        summary = json.loads((output / "summary.json").read_text())
        results = summary["results"]
        expected = {(f"T{i:02}", n) for i in range(1, 14) for n in range(1, 4)}
        if len(results) != 39 or {(r["case"], r["trial"]) for r in results} != expected:
            return False
        if len({r["report_id"] for r in results}) != 39 or not summary.get("passed"):
            return False
        inputs = [manifest_path]
        inputs += [manifest_path.parent / s["path"] for s in manifest["sources"]]
        for files in manifest["cases"].values():
            inputs += [
                manifest_path.parent / files["input"],
                manifest_path.parent / files["answer"],
            ]
        inputs += list((root / "backend/app").rglob("*.py")) + list(
            (root / "backend/app").rglob("*.md")
        )
        latest = max(path.stat().st_mtime for path in inputs)
        for row in results:
            if (
                not row.get("passed")
                or not row.get("code_passed")
                or not row.get("human_checked")
                or not row.get("judge", {}).get("passed")
                or row.get("judge", {}).get("redline")
                or row.get("error")
                or (output / f"{row['report_id']}.json").stat().st_mtime < latest
            ):
                return False
        return bool(cases)
    except (OSError, ValueError, KeyError, TypeError):
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "阶段2-2":
        print("N/A（本阶段无原13题专项闸门）")
        raise SystemExit(0)
    if not check():
        print(
            "问题：原13题×3真实评测缺失、未全过、待人工复核或早于当前代码；阶段2-2仍待验。"
        )
        raise SystemExit(2)
    print("通过：原13题各3次、独立评审、人工复核及结果时效均已核对。")
