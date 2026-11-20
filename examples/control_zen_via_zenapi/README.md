# Control ZEN through ZEN-API

Examples for microscope control, experiment configuration, image streaming,
and guided acquisition.

**These scripts can move hardware, acquire images, and change or overwrite
experiment/output files. Review their settings before running on a microscope.**

## Setup and launch

- Install the workshop's `pixi_smartmic:default` environment; see the
  [workshop setup](../../README.md).
- Start a compatible ZEN installation with ZEN-API enabled.
- Configure the gateway INI beside the selected script. Examples use either
  `config.ini` or `my_config.ini`; check the script's selected path.
- Adapt experiment names, stage coordinates, input files, and output paths
  to your microscope. Examples that open images in ZEN also need TCP/IP access.

From **Windows CMD**, launch a selected script using the workshop environment:

```bat
cd /d "F:\Pixi_Projects\zen_czi\smartmicroscopy"
pixi run --locked -e default python "examples\control_zen_via_zenapi\zenapi_streaming.py"
```

Replace the script name with the example you need. Most examples are configured
inside their source rather than through command-line flags.

## Hardware and experiment examples

### Offline position extraction

[extract_experiment_positions.py](extract_experiment_positions.py) reads the
well-plate [experiment](../../data/ZEN_API_OMEZARR_Stream_Wellplate.czexp)
without connecting to ZEN or moving hardware. It reuses
`zen_api_utils.extract_czexp_plan`, rather than the CZI-image metadata tools
in czitools.

From the workshop root:

```powershell
pixi run --locked -e default python examples\control_zen_via_zenapi\extract_experiment_positions.py
pixi run --locked -e default python examples\control_zen_via_zenapi\extract_experiment_positions.py --output results\experiment_positions.csv
```

An optional positional argument selects another CZEXP. The default experiment
contains 24 acquisition positions in 12 wells, with two fields per well.
Every run displays a formatted terminal table, with coordinates rounded to
three decimal places. `--output` additionally saves the full-precision CSV.
CSV rows include block/scene indices, containing well ID and row/column,
field index within the well, position name/ID, and XYZ stage coordinates in
micrometers. Block/scene and well row/column indices are zero-based; field
indices are one-based. These are absolute stage coordinates, not offsets
from the well center. The zero-based `well_index` numbers acquired wells
consecutively in first-appearance order within each acquisition block,
without gaps for skipped wells. In this experiment C4 = 0, D5 = 1, and
E6 = 2. All positions within a well share its index. It appears in both
the terminal table and CSV; well row/column still describe the full plate.
Positions without well assignments have empty well
fields. Only enabled acquisition positions in active blocks/setups are
included, not every possible well of the carrier. An existing output CSV is
replaced; the input experiment is never modified.

For rectangular tile regions, `tile_width_um` and `tile_height_um` contain
the full region's `ContourSize` in micrometers (not individual camera-tile
dimensions). Both appear in the terminal table and CSV; they are empty in
CSV and shown as `-` in the table for single positions and nonrectangular
regions. Missing or invalid rectangular dimensions raise an error.
For example:

```powershell
pixi run --locked -e default python examples\control_zen_via_zenapi\extract_experiment_positions.py data\ZEN_API_OMEZARR_Stream_Wellplate_Tiles.czexp
```

This [tile-region experiment](../../data/ZEN_API_OMEZARR_Stream_Wellplate_Tiles.czexp)
contains three regions (A1, C3, D1), each 548.8 by 396.48 micrometers.

#### Use from Python

For filtering, grouping, or notebook display, use the pandas DataFrame helper
(pandas is already included in the workshop environment). From the workshop root:

```python
from examples.control_zen_via_zenapi.extract_experiment_positions import (
    DEFAULT_EXPERIMENT,
    extract_positions_dataframe,
)

positions = extract_positions_dataframe(DEFAULT_EXPERIMENT)  # Also accepts a path string.
well_positions = positions.loc[positions["well_id"] == "C4"]
positions.to_csv("positions.csv", index=False)
```

The function returns full-precision values in the same column and acquisition
order as the CSV, without printing, saving files, or connecting to ZEN. Integer,
floating-point, and text columns use pandas nullable dtypes; missing values are
`pd.NA`, so use `isna()` or `notna()` to check them. Units and index conventions
are unchanged. Invalid experiments raise the same errors as the CLI.
For typed iteration, `extract_positions(path)` now returns immutable `Position`
dataclasses rather than dictionaries:

```python
from examples.control_zen_via_zenapi.extract_experiment_positions import extract_positions

for position in extract_positions(DEFAULT_EXPERIMENT):
    print(position.well_id, position.x, position.y, position.z)
    if position.tile_region is not None:
        region = position.tile_region
        print(region.contour_type, region.width_um, region.height_um)
```

`Position.x`, `.y`, and `.z` are absolute stage coordinates in micrometers.
`position.tile_region` is `None` for single positions; otherwise it is a
`TileRegion` dataclass containing the contour type and rectangular dimensions
in micrometers. Dimensions are `None` for nonrectangular regions; polygon
vertices and other shape-specific geometry are not extracted. These offline
models are distinct from `zen_api_utils.tiles_positions` models, whose
documented units are meters.

To migrate dictionary-based callers, use attributes such as `position.well_id`
and `position.x`, or `position.to_record()` for a dictionary using the unchanged
CSV/DataFrame column names. CLI and DataFrame output are unchanged.

### Live hardware examples

- **Acquire extracted positions:** [run_experiment_well_positions.py](run_experiment_well_positions.py)
  has an illustrated [workflow and per-well counting guide](well_position_count_docs/zen-well-position-guide.html)
  with standalone SVG diagrams and editable HTML source in the same folder. It
  creates a run folder under its configured `target_folder`, named
  `<experiment>_wellpositions_YYYY-MM-DD_HH-MM-SS` using UTC. With
  `run_experiment=True`, CZI output names follow
  `<experiment>_wellpositions_<well>_P004.czi`, with a one-based, three-digit
  acquisition index. The timestamp appears only in the run folder name.
  Positions without a well assignment use `Unassigned`. Existing files with
  the requested name in ZEN's output directory cause an error before acquisition
  rather than being overwritten. After all
  acquisitions succeed, the script moves their CZI files from ZEN's image output
  directory into that run folder, using the actual filenames returned by ZEN.
  Unrelated CZI files are left untouched. Existing run folders are never reused;
  missing files or destination conflicts raise an error before any moves.
  Acquisition failures leave completed files in ZEN's output directory.
  A move failure is reported and may leave files split between source and target;
  consult the logged paths. Extracted stage coordinates in micrometers are
  converted to meters for ZEN's `Position3D`; the log shows both values.
  With `save_cloned_experiment=True`, each configured clone is
  exported before acquisition as `position_0001.czexp`, etc., in the run folder.
  Open these snapshots in ZEN to inspect the actual acquisition settings.
  Snapshots are retained on acquisition failure, are never overwritten, and
  export errors stop the run before acquisition. They are local files, not
  replacements for the original experiment in ZEN's experiment list.
  Set `run_experiment=False` to configure and export diagnostics without
  acquiring images (the script still connects to ZEN). No CZI files are moved
  in this mode. If both flags are `False`, no run folder is created.
  After each acquisition, `czitools.read_tools.read_6darray` reads the first
  scene/time point/channel/Z plane. Bright objects are segmented using Otsu
  thresholding and the existing `ArrayProcessor` labeling helper (small-hole
  filling, removal of objects of at most 10 pixels, exclusion of border-touching
  objects). The count is logged with the well, position, and CZI path. This is
  a 2D grayscale count, not a sum over channels or a 3D count; RGB is rejected.
  Counts accumulate per well across acquired positions. Once a well reaches or
  exceeds `max_objects_per_well` (default: 100), its remaining positions are
  skipped before configuration, diagnostics, or acquisition. The script continues
  through the original list to the next eligible well/position; filenames retain
  the original position indices, so gaps are expected. The last image may take
  the count above the target. Positions without well assignments are not pooled
  or skipped by this limit. Totals and skipped positions are logged; the target
  must be positive. Without acquisition, no counts accumulate and all positions
  are still configured.
  `count_czi_objects(path, planes={"C": (1, 1), "Z": (2, 2)})` selects a
  different channel/Z plane. Ranges are inclusive and zero-based; omitted
  S/T/C/Z axes default to `(0, 0)`. Select one index per axis, not a stack.
  Read/analysis errors stop the run and leave acquired files in ZEN's output
  directory rather than reporting a misleading zero count.
  The source is organized as settings, image counting, diagnostic export, output
  collection, and the `main()` orchestration. Position extraction happens once
  inside `main()` rather than while loading the module. Adjust the settings at
  the top of the script before starting it.
  At startup, the complete extracted position list is displayed as a terminal
  table before connecting to ZEN, using the offline extraction CLI's formatting.
- **Changers:** [zenapi_changers.py](zenapi_changers.py) queries and changes
  optical components; [zenapi_objectivechanger.py](zenapi_objectivechanger.py)
  demonstrates objective and optovar positions.
- **Stage and focus:** [zenapi_stage_LM.py](zenapi_stage_LM.py) moves the XY stage;
  [zenapi_zdrive.py](zenapi_zdrive.py) controls focus/surface finding;
  [zenapi_stage_LM_stagemarks.py](zenapi_stage_LM_stagemarks.py) visits positions
  from a stage-mark file.
- **Acquisition settings:** [zenapi_positions.py](zenapi_positions.py) adds
  acquisition positions; [zenapi_tileregion.py](zenapi_tileregion.py) modifies
  tile regions; [zenapi_zstack.py](zenapi_zstack.py) modifies and runs Z-stacks.
- **Autofocus and monitoring:** [zenapi_swaf.py](zenapi_swaf.py) configures and
  runs software autofocus; [zenapi_experiment_status.py](zenapi_experiment_status.py)
  starts an experiment and monitors its status.

## Streaming

- [zenapi_streaming.py](zenapi_streaming.py): live PyQtGraph display with
  optional thresholding, semantic segmentation, or denoising.
- [zenapi_stream2omezarr_experiment.py](zenapi_stream2omezarr_experiment.py):
  experiment-metadata-driven OME-Zarr streaming, with a CLI and Python API.
  Run it with `--help` for arguments; see the
  [CMD example below](#cmd-streaming-example).
- [zenapi_stream_omezarr_simple.py](zenapi_stream_omezarr_simple.py):
  configured Python example with optional pyramids and output validation.

## Guided acquisition

[zenapi_guidedacq.py](zenapi_guidedacq.py) segments a saved overview and runs
live detail scans for the detected regions. Optional settings collect run
files and show a final comparison plot.

See the **[guided-acquisition README](guided_acquisition_docs/README_guided_acquisition.md)**
for setup, options, output locations, and the linked visual walkthrough,
flowchart, and sequence diagram.

## Supporting files

- [processing_tools.py](processing_tools.py): image processing, segmentation,
  object measurements, and stage-coordinate conversion.
- [onnx_inference.py](onnx_inference.py): ONNX model inference helpers.
- [StageMarks_Brainslide_CD7.czstm](StageMarks_Brainslide_CD7.czstm): example
  stage marks; verify coordinates against your sample before use.

Keep gateway credentials private. Output locations vary by example; check
the script and ZEN's configured image output directory before acquisition.

## CMD streaming example

Start a compatible ZEN installation with ZEN-API enabled and make the named
experiment available. Configure [config.ini](config.ini) for your gateway
(or create the local `my_config.ini` if selected by your script).
The gateway INI is required; streaming dimensions come from ZEN's exported
experiment metadata, not a separate streaming INI.

From the workshop root in **CMD**, start acquisition and write OME-Zarr:

```bat
pixi run --locked -e default python "examples\control_zen_via_zenapi\zenapi_stream2omezarr_experiment.py" ^
  --experiment "00_ZEN_API_OMEZARR_Stream" ^
  --zenapi-config "examples\control_zen_via_zenapi\config.ini" ^
  --output-dir "results\zen-streaming" ^
  --dtype uint16 ^
  --czi-name "zenapi_stream" ^
  --start-experiment ^
  --no-overwrite-czi ^
  --no-overwrite-zarr
```

- CMD's `^` continues a line; do not put spaces after it. Alternatively, join
  the lines with spaces and remove the carets.
- Replace the experiment name (without `.czexp`) and match `--dtype` to its pixels.
  Quote paths with spaces. `--output-dir` affects OME-Zarr, not ZEN's CZI location.
- For manual startup, use `--no-start-experiment` (the default), launch the script,
  then start the experiment in ZEN. Increase the default 30-second inactivity
  timeout with `--inactivity-timeout 120` if needed.
- Both `--no-overwrite-*` flags protect existing outputs; overwriting is otherwise
  enabled by default.
- Optional: `--validate-ome-zarr` validates metadata; `--pyramid-levels 3` adds
  three downsampled levels afterward. Explicit sample-carrier wells select an
  HCS plate layout by default.
