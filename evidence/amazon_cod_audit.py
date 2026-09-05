"""Audit the exact COD slice in the public Boss Leathers Amazon seller export.

This is deliberately an audit, not the final accuracy benchmark: the source has
only 47 terminal COD orders. It proves that CodGate's target/intervention has a
real public COD/return-to-seller analogue without pretending 47 rows are enough
to establish production model quality.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import amazon_cod_rto as source


def run(cache_dir: Path = Path(".cache/amazon-cod-rto"), output_dir: Path = Path("artifacts/amazon-cod-audit")) -> dict:
    archive = source.download_dataset(cache_dir)
    diagnostics = []
    best = None
    with zipfile.ZipFile(archive) as zf:
        members = [m for m in zf.namelist() if Path(m).suffix.lower() in {".xlsx", ".xlsm", ".csv"}]
    for member in sorted(members):
        try:
            frame = source._read_member(archive, member)
            columns = source.resolve_columns(frame)
            status = frame[columns.order_status].map(source._status)
            cod = frame[columns.cod].map(source._is_cod)
            terminal = status.isin({source.POSITIVE, source.NEGATIVE})
            mask = terminal & cod
            returned = int((mask & status.eq(source.POSITIVE)).sum())
            delivered = int((mask & status.eq(source.NEGATIVE)).sum())
            row = {
                "member": member,
                "rows": int(len(frame)),
                "terminal_rows": int(terminal.sum()),
                "cod_terminal_rows": int(mask.sum()),
                "cod_returned_to_seller": returned,
                "cod_delivered_to_buyer": delivered,
            }
            diagnostics.append(row)
            if best is None or row["cod_terminal_rows"] > best["cod_terminal_rows"]:
                best = row
        except Exception as exc:
            diagnostics.append({"member": member, "error": str(exc)})
    if best is None:
        raise RuntimeError("No Amazon seller source member matched the published schema")
    report = {
        "schema_version": 1,
        "role": "exact COD source audit; not final accuracy benchmark",
        "source": {
            "dataset_slug": source.DATASET_SLUG,
            "dataset_page": source.DATASET_PAGE,
            "zip_sha256": source._sha256(archive),
            "zip_bytes": archive.stat().st_size,
            "license": "CC0: Public Domain (per Kaggle dataset card)",
        },
        "exact_cod_slice": best,
        "diagnostics": diagnostics,
        "final_benchmark_eligible": False,
        "reason": "47 terminal COD orders are useful as an exact-domain audit but too small for a credible final accuracy claim.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    run()
