"""Export acquisition positions and their containing wells from a ZEN CZEXP.

Uses zen_api_utils' offline experiment parser; no running ZEN is required.
Only positions enabled for acquisition in active blocks/setups are included.
Stage coordinates are preserved as stored in the experiment (micrometers).
Rectangular tile-region width and height are exported in micrometers.
Use extract_positions_dataframe() for a pandas table without printing or writing.
"""

import argparse
import csv
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rich import box
from rich.console import Console
from rich.table import Table
from zen_api_utils.extract_czexp_plan import extract_plan, find_active_dimension_setup

WORKSHOP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENT = WORKSHOP_ROOT / "data" / "ZEN_API_OMEZARR_Stream_Wellplate.czexp"
CSV_FIELDS: tuple[str, ...] = (
    "block_index",
    "scene_index",
    "well_id",
    "well_row",
    "well_column",
    "well_index",
    "field_index",
    "position_name",
    "position_id",
    "stage_x_um",
    "stage_y_um",
    "stage_z_um",
    "tile_width_um",
    "tile_height_um",
)


@dataclass(frozen=True, slots=True)
class TileRegion:
    """Extracted contour metadata; rectangular dimensions are in micrometers.

    Dimensions are None for nonrectangular contours. Polygon vertices and other
    shape-specific geometry are not extracted.
    """

    contour_type: str | None
    width_um: float | None = None
    height_um: float | None = None


@dataclass(frozen=True, slots=True)
class Position:
    """An acquisition field with absolute stage coordinates x, y, z in micrometers."""

    block_index: int
    scene_index: int
    well_id: str | None
    well_row: int | None
    well_column: int | None
    well_index: int | None
    field_index: int
    position_name: str | None
    position_id: str | None
    x: float
    y: float
    z: float
    tile_region: TileRegion | None = None

    def to_record(self) -> dict[str, int | float | str | None]:
        """Flatten this position into the existing CSV/DataFrame schema."""
        return {
            "block_index": self.block_index,
            "scene_index": self.scene_index,
            "well_id": self.well_id,
            "well_row": self.well_row,
            "well_column": self.well_column,
            "well_index": self.well_index,
            "field_index": self.field_index,
            "position_name": self.position_name,
            "position_id": self.position_id,
            "stage_x_um": self.x,
            "stage_y_um": self.y,
            "stage_z_um": self.z,
            "tile_width_um": self.tile_region.width_um if self.tile_region is not None else None,
            "tile_height_um": self.tile_region.height_um if self.tile_region is not None else None,
        }


def extract_positions(experiment: str | Path) -> list[Position]:
    """Return one Position per planned field, retaining block and scene order.

    Block, scene, well row, and well column indices are zero-based.
    Wells are numbered consecutively in first-appearance order per block.
    Field indices are one-based; well fields are empty for unassigned positions.
    tile_region is None for single positions. For regions it holds contour
    metadata, with full-region dimensions only for rectangular contours.
    """
    experiment = Path(experiment)
    plan = extract_plan(experiment)
    xml_blocks = ET.parse(experiment).getroot().findall("./ExperimentBlocks/AcquisitionBlock")
    positions: list[Position] = []
    for block in plan["acquisition_blocks"]:
        setup = find_active_dimension_setup(xml_blocks[block["block_index"]], "RegionsSetup")
        regions = (
            {
                region.get("Id"): region
                for region in setup.findall("./SampleHolder/TileRegions/TileRegion")
                if region.get("Id") is not None
            }
            if setup is not None
            else {}
        )
        well_indices: dict[str, int] = {}
        for position in block["positions"]:
            width = height = None
            region = regions.get(position["position_id"])
            contour = region.find("Contour") if region is not None else None
            contour_type = contour.get("Type") if contour is not None else None
            if region is not None and contour_type == "Rectangle":
                try:
                    width, height = (float(value) for value in (region.findtext("ContourSize") or "").split(","))
                    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                        raise ValueError("Dimensions must be finite and positive")
                except ValueError as error:
                    raise ValueError(
                        f"Invalid rectangular tile-region ContourSize in {experiment}, "
                        f"block {block['block_index']}, region {position['position_id']}"
                    ) from error
            stage = position["stage_position"]
            well_id = position["well_id"]
            well_index = well_indices.setdefault(well_id, len(well_indices)) if well_id else None
            positions.append(
                Position(
                    block_index=block["block_index"],
                    scene_index=position["scene_index"],
                    well_id=well_id,
                    well_row=position["well_row"] - 1 if position["well_row"] is not None else None,
                    well_column=position["well_column"] - 1 if position["well_column"] is not None else None,
                    well_index=well_index,
                    field_index=position["field_index"],
                    position_name=position["position_name"],
                    position_id=position["position_id"],
                    x=stage["x"],
                    y=stage["y"],
                    z=stage["z"],
                    tile_region=TileRegion(contour_type, width, height) if region is not None else None,
                )
            )
    if not positions:
        raise ValueError(f"No acquisition positions found in {experiment}")
    return positions


def extract_positions_dataframe(experiment: str | Path) -> pd.DataFrame:
    """Return acquisition positions as a pandas table without output side effects.

    Columns follow CSV_FIELDS, with the same units, ordering, and index semantics
    as extract_positions(). Nullable integer, float, and string columns use
    pd.NA for missing values. Parsing and validation errors propagate unchanged.
    """
    return pd.DataFrame.from_records(
        [position.to_record() for position in extract_positions(experiment)], columns=CSV_FIELDS
    ).astype(
        {
            "block_index": "Int64",
            "scene_index": "Int64",
            "well_id": "string",
            "well_row": "Int64",
            "well_column": "Int64",
            "well_index": "Int64",
            "field_index": "Int64",
            "position_name": "string",
            "position_id": "string",
            "stage_x_um": "Float64",
            "stage_y_um": "Float64",
            "stage_z_um": "Float64",
            "tile_width_um": "Float64",
            "tile_height_um": "Float64",
        }
    )


def print_positions(positions: list[Position], *, title: str) -> None:
    """Display already-extracted positions with the same formatting as the CLI."""
    table = Table(title=title, box=box.ASCII, header_style="bold")
    columns = (
        ("block_index", "Block"),
        ("scene_index", "Scene"),
        ("well_id", "Well"),
        ("well_index", "Well index (0-based)"),
        ("well_row", "Row (0-based)"),
        ("well_column", "Column (0-based)"),
        ("field_index", "Field"),
        ("position_name", "Position"),
        ("stage_x_um", "X (um)"),
        ("stage_y_um", "Y (um)"),
        ("stage_z_um", "Z (um)"),
        ("tile_width_um", "Tile width (um)"),
        ("tile_height_um", "Tile height (um)"),
    )
    for key, label in columns:
        alignment = "left" if key in {"well_id", "position_name"} else "right"
        table.add_column(label, justify=alignment)
    for position in positions:
        row = position.to_record()
        values = []
        for key, _ in columns:
            value = row[key]
            if isinstance(value, float):
                values.append(f"{value:.3f}")
            else:
                values.append("-" if value is None else str(value))
        table.add_row(*values)
    Console().print(table)


def main() -> None:
    """Display a terminal table and optionally save the complete CSV."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "experiment",
        nargs="?",
        type=Path,
        default=DEFAULT_EXPERIMENT,
        help="Input CZEXP (default: the workshop well-plate experiment)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Also save CSV here; existing files are replaced",
    )
    args = parser.parse_args()
    positions = extract_positions(args.experiment)
    if args.output is not None:
        if args.output.resolve() == args.experiment.resolve():
            parser.error("--output must not overwrite the input experiment")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(position.to_record() for position in positions)
    print_positions(positions, title=args.experiment.name)
    wells = {position.well_id for position in positions if position.well_id}
    print(
        f"Extracted {len(positions)} positions in {len(wells)} wells"
        + (f" -> {args.output}" if args.output is not None else ""),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
