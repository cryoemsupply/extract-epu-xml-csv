# extract_cryoem_xml_metadata.py

Extract metadata from Thermo/FEI cryo-EM `MicroscopeImage` XML files into one flat CSV for downstream analysis.

## What it does

- Recursively scans a directory for XML files (by default).
- Parses each XML into a single row in a combined CSV.
- Flattens nested XML tags into slash-delimited column names.
- Expands `CustomData` and `CameraSpecificInput` key/value blocks into explicit fields.
- Prints a run summary with common collection/session metrics.

## Requirements

- Python 3.9+ (standard library only; no external Python packages required).

Optional local environment used in this repo:

```bash
conda activate cryoem
```

## Usage

```bash
python extract_cryoem_xml_metadata.py [-h] [-o OUTPUT] [--pattern PATTERN] [--no-recursive] input_dir
```

### Arguments

- `input_dir` (required): directory containing XML files.
- `-o, --output`: output CSV path (default: `./combined_xml_metadata.csv`).
- `--pattern`: glob pattern for XML files (default: `*.xml`).
- `--no-recursive`: disable recursive scan.

## Examples

Scan an EPU export recursively and write combined metadata:

```bash
python extract_cryoem_xml_metadata.py ./example_epu -o all_xml_metadata.csv
```

Only process foil-hole XML files:

```bash
python extract_cryoem_xml_metadata.py ./example_epu --pattern "FoilHole*.xml" -o foilhole_metadata.csv
```

Scan only the top-level directory (no subfolders):

```bash
python extract_cryoem_xml_metadata.py ./example_epu --no-recursive -o top_level_only.csv
```

## Output CSV format

Each parsed XML becomes one row. The output includes:

- `source_xml_rel`: XML path relative to `input_dir`.
- `source_xml`: absolute XML path.
- Flattened metadata fields such as:
  - `MicroscopeImage/microscopeData/instrument/InstrumentModel`
  - `MicroscopeImage/SpatialScale/pixelSize/x/numericValue`
  - `MicroscopeImage/CustomData/Detectors[EF-Falcon].TotalDose`
  - `MicroscopeImage/CameraSpecificInput/SuperResolutionFactor`

### Field naming rules

- Nested tags are joined with `/`.
- Repeated sibling tags are indexed (`Tag[1]`, `Tag[2]`, ...).
- If the same output field appears with different values, additional columns are created as `__dup2`, `__dup3`, etc.
- `CustomData` and `CameraSpecificInput` `<Key>/<Value>` entries are promoted to named columns (for example `MicroscopeImage/CustomData/Aperture[C2].Name`).
- `xsi:nil="true"` nodes are kept as empty strings.

## Console summary

After writing the CSV, the script prints a high-level session summary:

- Microscope model
- Magnification
- Pixel size (A)
- Super resolution mode
- Total dose (e-/A^2)
- Estimated total frames
- Exposure time
- Defocus range (um)
- Multishot scheme
- C2 aperture
- Energy filter slit info
- Movies collected

## Error handling and exit codes

- `0`: success.
- `1`: no matching files, all files failed parsing, or parse-related failure state.
- `2`: input directory does not exist or is not a directory.

If some files fail to parse, successful files are still written and skipped files are listed at the end.
