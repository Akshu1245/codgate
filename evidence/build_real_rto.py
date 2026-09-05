"""Robust entrypoint for building CodGate's external Meesho RTO evidence.

The Kaggle archive can contain more than one CSV and the source export uses
mixed date representations. This entrypoint inspects every CSV that matches the
published schema, selects the member with the largest valid terminal population,
normalizes dates deterministically, and then delegates modeling/evaluation to
`evidence.real_rto`.
"""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from pathlib import Path

import pandas as pd

from . import real_rto as core


def parse_order_dates(series: pd.Series) -> pd.Series:
    """Parse mixed source dates without letting the first row dictate one format."""
    # pandas >=2 supports format="mixed". The explicit option is important here:
    # the Meesho export contains multiple date representations.
    return pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")


def terminal_mask(frame: pd.DataFrame, columns: core.ResolvedColumns) -> pd.Series:
    statuses = frame[columns.status].map(core._norm_status)
    return statuses.isin({core.TERMINAL_DELIVERED, core.TERMINAL_RTO})


def candidate_quality(frame: pd.DataFrame, columns: core.ResolvedColumns) -> dict:
    mask = terminal_mask(frame, columns)
    dates = parse_order_dates(frame.loc[mask, columns.order_date])
    statuses = frame.loc[mask, columns.status].map(core._norm_status)
    valid = dates.notna()
    valid_statuses = statuses.loc[valid]
    return {
        "rows": int(len(frame)),
        "terminal_rows": int(mask.sum()),
        "terminal_with_valid_date": int(valid.sum()),
        "delivered_with_valid_date": int((valid_statuses == core.TERMINAL_DELIVERED).sum()),
        "rto_with_valid_date": int((valid_statuses == core.TERMINAL_RTO).sum()),
    }


def select_source_frame(archive: Path) -> tuple[pd.DataFrame, str, core.ResolvedColumns, list[dict]]:
    with zipfile.ZipFile(archive) as zf:
        members = sorted(name for name in zf.namelist() if name.lower().endswith(".csv"))
    if not members:
        raise RuntimeError("No CSV files found inside Kaggle dataset archive")

    diagnostics: list[dict] = []
    candidates: list[tuple[int, int, str, pd.DataFrame, core.ResolvedColumns]] = []
    for member in members:
        try:
            frame = core._read_csv_from_zip(archive, member)
            columns = core.resolve_columns(frame)
            core.assert_no_leakage(columns)
            quality = candidate_quality(frame, columns)
            diagnostics.append({"member": member, **quality, "columns": list(frame.columns)})
            both_classes = min(quality["delivered_with_valid_date"], quality["rto_with_valid_date"])
            candidates.append(
                (
                    quality["terminal_with_valid_date"],
                    both_classes,
                    member,
                    frame,
                    columns,
                )
            )
        except Exception as exc:
            diagnostics.append({"member": member, "error": str(exc)})

    if not candidates:
        raise RuntimeError("No CSV in the Kaggle archive matches the published Meesho schema")

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    valid_count, both_classes, member, frame, columns = candidates[0]
    if valid_count < 200 or both_classes < 1:
        raise RuntimeError(
            "No source member has a meaningful terminal DELIVERED/RTO population: "
            + json.dumps(diagnostics, default=str)
        )

    # Normalize the mixed source representation to one stable representation so
    # downstream code cannot silently discard rows due to date-format inference.
    parsed = parse_order_dates(frame[columns.order_date])
    frame = frame.copy()
    frame[columns.order_date] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")
    return frame, member, columns, diagnostics


def run(cache_dir: Path, output_dir: Path) -> dict:
    archive = core.download_dataset(cache_dir)
    frame, member, columns, diagnostics = select_source_frame(archive)

    source_manifest = {
        "dataset_slug": core.DATASET_SLUG,
        "dataset_page": core.DATASET_PAGE,
        "download_url": core.DATASET_DOWNLOAD,
        "zip_sha256": core._sha256(archive),
        "zip_bytes": archive.stat().st_size,
        "csv_member": member,
        "source_rows": int(len(frame)),
        "dataset_page_claim": "Kaggle page describes it as real Meesho supplier order data",
        "dataset_page_license_text": "Data files © Original Authors",
        "source_selection": "CSV with largest terminal DELIVERED/RTO_COMPLETE population after mixed-date parsing",
    }

    report, model_artifact, predictions = core.train_and_evaluate(frame, columns, source_manifest)
    report["source_diagnostics"] = diagnostics

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "report.md").write_text(core.report_markdown(report), encoding="utf-8")
    (output_dir / "model.json").write_text(json.dumps(model_artifact, separators=(",", ":")), encoding="utf-8")
    predictions.to_csv(output_dir / "heldout_predictions.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    (output_dir / "source-manifest.json").write_text(json.dumps(source_manifest, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/real-rto"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/real-rto-evidence"))
    args = parser.parse_args()
    report = run(args.cache_dir, args.output_dir)
    heldout = report["heldout_test"]
    print(core.report_markdown(report))
    print("SOURCE_DIAGNOSTICS " + json.dumps(report["source_diagnostics"], default=str))
    print(
        "REAL_RTO_METRICS "
        + json.dumps(
            {
                "n": heldout["n"],
                "precision": heldout["precision"],
                "recall": heldout["recall"],
                "f1": heldout["f1"],
                "average_precision": heldout["average_precision"],
                "tp": heldout["tp"],
                "fp": heldout["fp"],
                "fn": heldout["fn"],
                "tn": heldout["tn"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
