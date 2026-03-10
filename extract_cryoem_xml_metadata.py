#!/usr/bin/env python3
"""Extract Thermo/FEI cryo-EM image XML metadata into a single CSV.

This script scans a directory for XML files, extracts per-image metadata from each
`MicroscopeImage` XML, and writes one combined CSV where each XML is one row.
Usage: python extract_cryoem_xml_metadata.py <input_directory> -o <output_csv>
"""

from __future__ import annotations

import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from statistics import mean
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XSI_NIL_ATTR = f"{{{XSI_NS}}}nil"


def local_name(tag: str) -> str:
    """Return the namespace-stripped XML tag name."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _set_field(out: Dict[str, str], field: str, value: str) -> None:
    """Set a field, preserving all values if duplicates occur."""
    if field not in out:
        out[field] = value
        return
    if out[field] == value:
        return

    suffix = 2
    while f"{field}__dup{suffix}" in out:
        suffix += 1
    out[f"{field}__dup{suffix}"] = value


def extract_metadata_from_xml(xml_path: Path) -> Dict[str, str]:
    """Extract metadata fields from a single XML file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    out: Dict[str, str] = {}
    root_name = local_name(root.tag)

    def walk(element: ET.Element, current_path: str) -> None:
        children = list(element)

        # Keep explicit empty values for xsi:nil nodes.
        if element.attrib.get(XSI_NIL_ATTR) == "true":
            _set_field(out, current_path, "")

        if not children:
            if not (current_path.endswith("/Key") or current_path.endswith("/Value")):
                _set_field(out, current_path, (element.text or "").strip())
            return

        name_counts = Counter(local_name(child.tag) for child in children)
        seen = defaultdict(int)

        for child in children:
            child_name = local_name(child.tag)
            seen[child_name] += 1
            if name_counts[child_name] > 1:
                child_name = f"{child_name}[{seen[child_name]}]"
            walk(child, f"{current_path}/{child_name}")

    walk(root, root_name)

    # Flatten key/value blocks into explicit field names.
    for section in root.iter():
        section_name = local_name(section.tag)
        if section_name not in {"CustomData", "CameraSpecificInput"}:
            continue

        for entry in list(section):
            key = ""
            value = ""
            for kv_child in list(entry):
                kv_name = local_name(kv_child.tag)
                if kv_name == "Key":
                    key = (kv_child.text or "").strip()
                elif kv_name == "Value":
                    value = (kv_child.text or "").strip()

            if key:
                field_name = f"{root_name}/{section_name}/{key}"
                _set_field(out, field_name, value)

    return out


def iter_xml_files(input_dir: Path, pattern: str, recursive: bool) -> Iterable[Path]:
    """Yield XML file paths in deterministic order."""
    globber = input_dir.rglob if recursive else input_dir.glob
    files = sorted(p for p in globber(pattern) if p.is_file())
    return files


def build_rows(xml_files: Iterable[Path], base_dir: Path) -> Tuple[List[Dict[str, str]], List[Tuple[Path, str]]]:
    """Build output rows and collect parse failures."""
    rows: List[Dict[str, str]] = []
    failures: List[Tuple[Path, str]] = []

    for xml_file in xml_files:
        try:
            metadata = extract_metadata_from_xml(xml_file)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            failures.append((xml_file, str(exc)))
            continue

        metadata["source_xml"] = str(xml_file.resolve())
        metadata["source_xml_rel"] = str(xml_file.relative_to(base_dir))
        rows.append(metadata)

    return rows, failures


def write_csv(rows: List[Dict[str, str]], output_path: Path) -> int:
    """Write combined metadata rows to CSV and return number of columns."""
    priority_columns = ["source_xml_rel", "source_xml"]

    all_fields = set()
    for row in rows:
        all_fields.update(row.keys())

    ordered_fields = [c for c in priority_columns if c in all_fields]
    ordered_fields.extend(sorted(all_fields - set(ordered_fields)))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ordered_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return len(ordered_fields)


def parse_float(value: str) -> float | None:
    """Parse a float safely."""
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def non_empty_values(rows: List[Dict[str, str]], field: str) -> List[str]:
    """Get non-empty string values for a given field across all rows."""
    out: List[str] = []
    for row in rows:
        value = row.get(field, "").strip()
        if value:
            out.append(value)
    return out


def first_value(rows: List[Dict[str, str]], field: str, default: str = "N/A") -> str:
    """Get first non-empty value for a field."""
    values = non_empty_values(rows, field)
    return values[0] if values else default


def format_range(values: List[float], fmt: str) -> str:
    """Format either a single value or a min-max range."""
    if not values:
        return "N/A"
    lo = min(values)
    hi = max(values)
    if abs(hi - lo) < 1e-12:
        return fmt.format(lo)
    return f"{fmt.format(lo)} to {fmt.format(hi)}"


def print_run_summary(rows: List[Dict[str, str]]) -> None:
    """Print user-facing microscope/session summary metrics."""
    microscope = first_value(rows, "MicroscopeImage/microscopeData/instrument/InstrumentModel")
    magnification = first_value(rows, "MicroscopeImage/microscopeData/optics/TemMagnification/NominalMagnification")

    pixel_vals_m = [
        v for v in (
            parse_float(x)
            for x in non_empty_values(rows, "MicroscopeImage/SpatialScale/pixelSize/x/numericValue")
        )
        if v is not None
    ]
    pixel_size = "N/A"
    if pixel_vals_m:
        # 1 m = 1e10 Angstrom.
        pixel_vals_a = [v * 1e10 for v in pixel_vals_m]
        pixel_size = f"{format_range(pixel_vals_a, '{:.4f}')} A"

    super_res_factor = first_value(rows, "MicroscopeImage/CameraSpecificInput/SuperResolutionFactor")
    if super_res_factor == "N/A":
        super_res_mode = "N/A"
    else:
        f = parse_float(super_res_factor)
        if f is None:
            super_res_mode = super_res_factor
        elif f > 1:
            super_res_mode = f"On (factor {super_res_factor})"
        else:
            super_res_mode = f"Off (factor {super_res_factor})"

    total_dose_vals = [
        v for v in (
            parse_float(x)
            for x in non_empty_values(rows, "MicroscopeImage/CustomData/Detectors[EF-Falcon].TotalDose")
        )
        if v is not None
    ]
    total_dose = f"{format_range(total_dose_vals, '{:.3f}')}" + (" e-/A^2" if total_dose_vals else "")

    exposure_vals = [
        v for v in (
            parse_float(x)
            for x in non_empty_values(rows, "MicroscopeImage/microscopeData/acquisition/camera/ExposureTime")
        )
        if v is not None
    ]
    exposure_time = f"{format_range(exposure_vals, '{:.4f}')}" + (" s" if exposure_vals else "")

    frame_rates = [
        v for v in (
            parse_float(x)
            for x in non_empty_values(rows, "MicroscopeImage/CustomData/Detectors[EF-Falcon].FrameRate")
        )
        if v is not None
    ]
    total_frames = "N/A"
    if frame_rates and exposure_vals:
        # Estimate movie frames per image from frame rate * exposure.
        all_frames = []
        for row in rows:
            fr = parse_float(row.get("MicroscopeImage/CustomData/Detectors[EF-Falcon].FrameRate", ""))
            ex = parse_float(row.get("MicroscopeImage/microscopeData/acquisition/camera/ExposureTime", ""))
            if fr is None or ex is None:
                continue
            all_frames.append(fr * ex)
        if all_frames:
            rounded = [round(v) for v in all_frames]
            total_frames = format_range([float(v) for v in rounded], "{:.0f}")
            if len(all_frames) > 1:
                total_frames += f" (mean {mean(rounded):.0f})"

    defocus_vals_m = [
        v for v in (
            parse_float(x)
            for x in non_empty_values(rows, "MicroscopeImage/microscopeData/optics/Defocus")
        )
        if v is not None
    ]
    defocus_range = "N/A"
    if defocus_vals_m:
        # meters -> micrometers
        defocus_vals_um = [v * 1e6 for v in defocus_vals_m]
        defocus_range = f"{format_range(defocus_vals_um, '{:.3f}')} um"

    multishot_scheme = first_value(rows, "MicroscopeImage/CustomData/MultishotScheme", default="N/A (not found in XML)")
    c2_aperture = first_value(rows, "MicroscopeImage/CustomData/Aperture[C2].Name")

    slit_inserted = first_value(rows, "MicroscopeImage/microscopeData/optics/EnergyFilter/EnergySelectionSlitInserted")
    slit_width_vals = [
        v for v in (
            parse_float(x)
            for x in non_empty_values(rows, "MicroscopeImage/microscopeData/optics/EnergyFilter/EnergySelectionSlitWidth")
        )
        if v is not None
    ]
    slit = "N/A"
    if slit_width_vals:
        slit = f"{format_range(slit_width_vals, '{:.3f}')} eV"
        if slit_inserted != "N/A":
            slit += f" (inserted: {slit_inserted})"

    print()
    print(f"Microscope: {microscope}")
    print(f"Magnification: {magnification}")
    print(f"Pixel Size: {pixel_size}")
    print(f"Super Resolution Mode: {super_res_mode}")
    print(f"Total Dose: {total_dose}")
    print(f"Total Frames: {total_frames}")
    print(f"Exposure Time: {exposure_time}")
    print(f"Defocus Range: {defocus_range}")
    print(f"Multishot Scheme: {multishot_scheme}")
    print(f"C2 Aperture: {c2_aperture}")
    print(f"Slit: {slit}")
    print(f"Movies Collected: {len(rows)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract metadata from cryo-EM image XML files into one CSV."
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing XML files.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("combined_xml_metadata.csv"),
        help="Output CSV path (default: ./combined_xml_metadata.csv)",
    )
    parser.add_argument(
        "--pattern",
        default="*.xml",
        help="Glob pattern for XML files (default: *.xml)",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Disable recursive scan (default is recursive).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"ERROR: input directory does not exist: {input_dir}", file=sys.stderr)
        return 2

    xml_files = list(iter_xml_files(input_dir, args.pattern, recursive=not args.no_recursive))
    if not xml_files:
        print(f"No files matched pattern '{args.pattern}' in {input_dir}", file=sys.stderr)
        return 1

    rows, failures = build_rows(xml_files, input_dir)
    if not rows:
        print("No XML files could be parsed.", file=sys.stderr)
        for path, err in failures:
            print(f"  - {path}: {err}", file=sys.stderr)
        return 1

    output_path = args.output.resolve()
    n_columns = write_csv(rows, output_path)

    print(f"Wrote {len(rows)} rows x {n_columns} columns to: {output_path}")
    print_run_summary(rows)
    if failures:
        print(f"Skipped {len(failures)} file(s) due to parse errors:")
        for path, err in failures:
            print(f"  - {path}: {err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
