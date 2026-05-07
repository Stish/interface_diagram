# =============================================================================
# interface_diagram.py
# =============================================================================
# Reads a semicolon-delimited CSV file containing software component (SWC)
# interface definitions and generates a Draw.io (.drawio) diagram file.
#
# Each row in the CSV describes one interface between a providing and a
# consuming component.  The consuming component column may contain a
# comma-separated list of consumers for a single interface.
#
# The script supports two diagram modes:
#   overview  – All SWCs are placed on a grid.  Nodes are topology-sorted by
#               total interface count so the busiest components land in the
#               center rows.  Box sizes are driven by the number of connections
#               on each face.
#   focus     – A single named SWC is placed in the center.  All providers
#               appear on the left and all consumers on the right.
#
# Edge colors reflect the interface status column (if present in the CSV):
#   green  – Aligned
#   red    – Draft
#   grey   – Unknown / no status
#
# Edge notation can be selected via --notation:
#   arrow     – classic provider -> consumer arrow
#   lollipop  – provider-side lollipop and consumer-side socket marker
#
# A legend is rendered below every diagram explaining the arrow direction and
# color coding.
#
# Column headers in the CSV can be remapped via config.json (same directory).
# See config.json for the default mapping.
#
# -----------------------------------------------------------------------------
# Usage
# -----------------------------------------------------------------------------
#   py interface_diagram.py [OPTIONS]
#
# Options:
#   --input   PATH     Input CSV file  (default: data.csv)
#   --output  PATH     Output .drawio file  (default: interface_diagram.drawio)
#   --config  PATH     JSON config with column mapping  (default: config.json)
#   --mode    MODE     overview | focus  (default: overview)
#   --focus-swc NAME   SWC name to center on (required for --mode focus)
#   --status  FILTER   all | aligned | draft  (default: all)
#   --notation STYLE   arrow | lollipop  (default: arrow)
#
# Examples:
#   # Overview diagram of all interfaces
#   py interface_diagram.py --input example_data.csv --output example_overview.drawio
#
#   # Overview showing only aligned interfaces
#   py interface_diagram.py --input example_data.csv --output example_aligned.drawio --status aligned
#
#   # Focus diagram centered on a specific component
#   py interface_diagram.py --mode focus --focus-swc SWC_B --input example_data.csv --output example_focus_SWC_B.drawio
#
#   # Focus diagram with a custom column mapping
#   py interface_diagram.py --mode focus --focus-swc SWC_B --config my_config.json --input example_data.csv --output example_focus_SWC_B.drawio
#
#   # Overview with lollipop notation
#   py interface_diagram.py --notation lollipop --input example_data.csv --output example_focus_SWC_B_lollipop.drawio
#
#   # Focus diagram with lollipop notation and status filter
#   py interface_diagram.py --mode focus --focus-swc SWC_B --notation lollipop --status aligned --input example_data.csv --output example_focus_SWC_B_lollipop_aligned.drawio
#
# =============================================================================

from __future__ import annotations

import argparse
import csv
import json
import math
import xml.dom.minidom
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Maps internal field keys (used throughout the script) to the default CSV
# column header names.  Override individual headers in config.json.
_DEFAULT_COLUMN_MAP = {
	"id":       "ID",
	"provider": "Component providing",
	"consumer": "Component consuming",
	"name":     "Name",
	"status":   "Status",
}

# Maps internal status keys to the label used in the Excel/CSV status column.
# Override in config.json under "status_map".
_DEFAULT_STATUS_MAP = {
	"aligned": "aligned",
	"draft":   "draft",
}

# Hex colors for each internal status key and the fallback "unknown" bucket.
# Override in config.json under "status_colors".
_DEFAULT_STATUS_COLORS = {
	"aligned": "#1a7a1a",   # dark green
	"draft":   "#cc0000",   # red
	"unknown": "#666666",   # grey
}


def load_config(config_path: Path | None) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
	"""Load column mapping, status map, and status colors from a JSON config file.

	Returns a three-tuple (col_map, status_map, status_colors) where each dict
	is seeded from the built-in defaults and then overridden by any values found
	in the config file.
	"""
	if config_path is None or not config_path.exists():
		return dict(_DEFAULT_COLUMN_MAP), dict(_DEFAULT_STATUS_MAP), dict(_DEFAULT_STATUS_COLORS)
	with config_path.open("r", encoding="utf-8") as f:
		data = json.load(f)
	col_map = dict(_DEFAULT_COLUMN_MAP)
	col_map.update(data.get("column_map", {}))
	status_map = dict(_DEFAULT_STATUS_MAP)
	status_map.update(data.get("status_map", {}))
	status_colors = dict(_DEFAULT_STATUS_COLORS)
	status_colors.update(data.get("status_colors", {}))
	return col_map, status_map, status_colors


# One parsed row from the CSV.  The status field is normalised to lowercase
# so comparisons elsewhere are case-insensitive.
@dataclass
class InterfaceRow:
	interface_id: str
	provider: str
	consumer: str
	name: str
	status: str  # "aligned", "draft", or "" if the column is absent


class DrawIoBuilder:
	"""Builds a Draw.io XML document incrementally.

	All cell IDs are auto-incremented integers (starting at 2 because 0 and 1
	are reserved by Draw.io for the root and default parent cells).
	"""

	def __init__(self) -> None:
		# ID counter; 0 and 1 are the mandatory Draw.io root cells.
		self.next_id = 2

		# Build the mandatory XML skeleton: mxfile > diagram > mxGraphModel > root
		self.mxfile = ET.Element("mxfile", host="app.diagrams.net", modified="2026-05-05T00:00:00.000Z", agent="interface_diagram.py", version="24.7.5")
		self.diagram = ET.SubElement(self.mxfile, "diagram", id="interface-diagram", name="Interfaces")
		self.model = ET.SubElement(
			self.diagram,
			"mxGraphModel",
			dx="1800",
			dy="1000",
			grid="1",
			gridSize="10",
			guides="1",
			tooltips="1",
			connect="1",
			arrows="1",
			fold="1",
			page="1",
			pageScale="1",
			pageWidth="3300",   # A3 landscape width in points
			pageHeight="2550",  # A3 landscape height in points
			math="0",
			shadow="0",
		)
		self.root = ET.SubElement(self.model, "root")
		# Draw.io requires these two sentinel cells to exist.
		ET.SubElement(self.root, "mxCell", id="0")
		ET.SubElement(self.root, "mxCell", id="1", parent="0")

	def _new_id(self) -> str:
		"""Return the next available cell ID as a string and advance the counter."""
		value = str(self.next_id)
		self.next_id += 1
		return value

	def add_vertex(self, value: str, style: str, x: float, y: float, w: float, h: float, parent: str = "1") -> str:
		"""Add a shape (box/text/swatch) and return its generated cell ID."""
		cell_id = self._new_id()
		cell = ET.SubElement(self.root, "mxCell", id=cell_id, value=value, style=style, vertex="1", parent=parent)
		# mxGeometry must be passed as a plain dict; the `as` key cannot be a
		# keyword argument because it is a reserved word in Python.
		ET.SubElement(
			cell,
			"mxGeometry",
			{
				"x": f"{x:.2f}",
				"y": f"{y:.2f}",
				"width": f"{w:.2f}",
				"height": f"{h:.2f}",
				"as": "geometry",
			},
		)
		return cell_id

	def add_edge(
		self,
		source: str,
		target: str,
		value: str = "",
		style: str = "endArrow=none;html=1;",
		exit_xy: tuple[float, float] | None = None,   # (relX, relY) on source face, e.g. (1.0, 0.5)
		entry_xy: tuple[float, float] | None = None,  # (relX, relY) on target face
		label_x: float = 0.0,   # relative position along the edge [-0.5 … 0.5]
		label_y: float = 0.0,   # perpendicular pixel offset for the label
	) -> str:
		"""Add a directed edge between two cells and return its cell ID.

		exitX/Y and entryX/Y are appended to the style string so that Draw.io
		draws a straight line anchored at a specific point on each box face
		(avoids kinked or overlapping lines).
		"""
		full_style = style
		if exit_xy is not None:
			full_style += f"exitX={exit_xy[0]:.3f};exitY={exit_xy[1]:.3f};exitDx=0;exitDy=0;"
		if entry_xy is not None:
			full_style += f"entryX={entry_xy[0]:.3f};entryY={entry_xy[1]:.3f};entryDx=0;entryDy=0;"
		cell_id = self._new_id()
		cell = ET.SubElement(
			self.root,
			"mxCell",
			id=cell_id,
			value=value,
			style=full_style,
			edge="1",
			parent="1",
			source=source,
			target=target,
		)
		# relative=1 means label_x/y are edge-relative, not absolute coordinates.
		geo_attrs: dict[str, str] = {"relative": "1", "as": "geometry"}
		if label_x != 0.0:
			geo_attrs["x"] = f"{label_x:.3f}"  # shifts label along the edge
		if label_y != 0.0:
			geo_attrs["y"] = f"{label_y:.1f}"  # shifts label above/below the edge
		ET.SubElement(cell, "mxGeometry", geo_attrs)
		return cell_id

	def add_edge_attached_symbol(
		self,
		edge_id: str,
		value: str,
		style: str,
		rel_x: float,
		offset_x: float,
		offset_y: float,
		w: float,
		h: float,
	) -> str:
		"""Add a vertex attached to an edge so it moves with that edge."""
		cell_id = self._new_id()
		cell = ET.SubElement(
			self.root,
			"mxCell",
			id=cell_id,
			value=value,
			style=style,
			vertex="1",
			parent=edge_id,
			connectable="0",
		)
		geo = ET.SubElement(
			cell,
			"mxGeometry",
			{
				"x": f"{rel_x:.3f}",
				"y": "0",
				"width": f"{w:.2f}",
				"height": f"{h:.2f}",
				"relative": "1",
				"as": "geometry",
			},
		)
		ET.SubElement(
			geo,
			"mxPoint",
			{
				"x": f"{offset_x:.2f}",
				"y": f"{offset_y:.2f}",
				"as": "offset",
			},
		)
		return cell_id

	def to_xml_string(self) -> str:
		"""Convert the XML tree to a formatted string with proper indentation and linebreaks."""
		# First, serialize to a raw string.
		raw_xml = ET.tostring(self.mxfile, encoding="unicode")
		# Parse it with minidom and pretty-print with 2-space indentation.
		dom = xml.dom.minidom.parseString(raw_xml)
		return dom.toprettyxml(indent="  ")

	def add_legend(
		self,
		x: float,
		y: float,
		notation: str = "arrow",
		status_colors: dict[str, str] | None = None,
		status_map: dict[str, str] | None = None,
	) -> None:
		"""Render a self-contained legend box explaining arrow direction and status colors."""
		if status_colors is None:
			status_colors = _DEFAULT_STATUS_COLORS
		if status_map is None:
			status_map = _DEFAULT_STATUS_MAP
		pad = 14.0
		swc_w, swc_h = 90.0, 36.0
		arrow_gap = 60.0          # horizontal space between Provider and Consumer boxes
		swatch_w, swatch_h = 20.0, 20.0
		row_gap = 10.0            # vertical gap between color rows
		section_gap = 16.0       # vertical gap between sections

		# Heights of each section
		title_h = 28.0
		dir_section_h = swc_h + 24.0   # label above + boxes
		color_section_h = 3 * (swatch_h + row_gap) - row_gap

		box_w = pad + swc_w + arrow_gap + swc_w + pad
		box_h = pad + title_h + section_gap + dir_section_h + section_gap + 18.0 + color_section_h + pad

		# ── Outer container ──────────────────────────────────────────────────
		self.add_vertex(
			"",
			"rounded=1;html=1;strokeColor=#000000;fillColor=#f5f5f5;strokeWidth=1.5;",
			x, y, box_w, box_h,
		)
		self.add_vertex(
			"<b>Legend</b>",
			"text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=13;",
			x + pad,
			y + 6.0,
			box_w - 2 * pad,
			title_h,
		)

		# ── Section 1: Transition direction ──────────────────────────────────
		sec1_y = y + pad + title_h + section_gap

		# Section label
		self.add_vertex(
			"Transition direction",
			"text;html=1;strokeColor=none;fillColor=none;align=left;"
			"verticalAlign=middle;fontStyle=1;fontSize=11;",
			x + pad, sec1_y, box_w - 2 * pad, 18.0,
		)

		# Provider box  |  arrow  |  Consumer box
		boxes_y = sec1_y + 22.0
		src_id = self.add_vertex(
			"Provider",
			"rounded=1;whiteSpace=wrap;html=1;strokeWidth=2;"
			"fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=11;",
			x + pad, boxes_y, swc_w, swc_h,
		)
		tgt_id = self.add_vertex(
			"Consumer",
			"rounded=1;whiteSpace=wrap;html=1;strokeWidth=2;"
			"fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=11;",
			x + pad + swc_w + arrow_gap, boxes_y, swc_w, swc_h,
		)
		# Direction connector preview between provider and consumer.
		edge_id = self._new_id()
		arrow_cell = ET.SubElement(
			self.root, "mxCell",
			id=edge_id, value="",
			style=f"{direction_style(notation, '#555555')}"
				  f"exitX=1;exitY=0.5;exitDx=0;exitDy=0;"
				  f"entryX=0;entryY=0.5;entryDx=0;entryDy=0;",
			edge="1", parent="1", source=src_id, target=tgt_id,
		)
		ET.SubElement(arrow_cell, "mxGeometry", {"relative": "1", "as": "geometry"})
		if notation == "lollipop":
			add_center_lollipop(self, edge_id, 0.0, "#555555")


		# ── Section 2: Status colors ─────────────────────────────────────────
		sec2_y = sec1_y + dir_section_h + section_gap

		self.add_vertex(
			"Status",
			"text;html=1;strokeColor=none;fillColor=none;align=left;"
			"verticalAlign=middle;fontStyle=1;fontSize=11;",
			x + pad, sec2_y, box_w - 2 * pad, 18.0,
		)

		swatches = [
			(status_colors.get("aligned", _DEFAULT_STATUS_COLORS["aligned"]), status_map.get("aligned", "aligned")),
			(status_colors.get("draft",   _DEFAULT_STATUS_COLORS["draft"]),   status_map.get("draft",   "draft")),
			(status_colors.get("unknown", _DEFAULT_STATUS_COLORS["unknown"]), "Unknown / no status"),
		]
		swatch_x = x + pad
		label_x  = x + pad + swatch_w + 8.0
		label_w  = box_w - 2 * pad - swatch_w - 8.0
		row_y = sec2_y + 22.0
		for color, label in swatches:
			self.add_vertex(
				"",
				f"rounded=1;strokeColor={color};fillColor={color};",
				swatch_x, row_y, swatch_w, swatch_h,
			)
			self.add_vertex(
				label,
				f"text;html=1;strokeColor=none;fillColor=none;align=left;"
				f"verticalAlign=middle;fontSize=11;fontColor={color};",
				label_x, row_y, label_w, swatch_h,
			)
			row_y += swatch_h + row_gap


def split_consumers(raw_consumer: str) -> list[str]:
	"""Split a potentially comma-separated consumer cell into individual names."""
	# The CSV allows multiple consumers for the same interface in one cell,
	# separated by commas.  Empty tokens (e.g. trailing commas) are ignored.
	return [part.strip() for part in raw_consumer.split(",") if part.strip()]


def parse_csv(
	input_csv: Path,
	col: dict[str, str] | None = None,
	status_reverse_map: dict[str, str] | None = None,
) -> list[InterfaceRow]:
	"""Read the CSV and return one InterfaceRow per (provider, consumer) pair.

	Rows with an empty provider column are silently skipped.
	A single CSV row may expand into multiple InterfaceRows if the consumer
	cell contains a comma-separated list.

	status_reverse_map maps Excel status values (lowercased) to internal keys
	such as "aligned" or "draft".  Built from the inverted status_map config.
	"""
	if col is None:
		col = dict(_DEFAULT_COLUMN_MAP)
	if status_reverse_map is None:
		status_reverse_map = {v.lower(): k for k, v in _DEFAULT_STATUS_MAP.items()}
	rows: list[InterfaceRow] = []
	# utf-8-sig transparently strips the BOM that Excel adds when saving as CSV.
	with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
		reader = csv.DictReader(handle, delimiter=";")
		# Validate that all required columns are present before iterating.
		required_headers = {col["id"], col["provider"], col["consumer"], col["name"]}
		missing = required_headers - set(reader.fieldnames or [])
		if missing:
			raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
		# Status is optional — diagrams are still valid without it.
		has_status = col["status"] in (reader.fieldnames or [])

		for raw in reader:
			provider = (raw.get(col["provider"]) or "").strip()
			name = (raw.get(col["name"]) or "").strip()
			interface_id = (raw.get(col["id"]) or "").strip()
			# Normalise status: strip, lowercase, then map Excel label -> internal key.
			raw_status = (raw.get(col["status"]) or "").strip().lower() if has_status else ""
			status = status_reverse_map.get(raw_status, raw_status)

			# Skip rows that have no provider (e.g. blank separator rows).
			if not provider:
				continue

			# One CSV row may describe the same interface for several consumers.
			for consumer in split_consumers((raw.get(col["consumer"]) or "").strip()):
				rows.append(
					InterfaceRow(
						interface_id=interface_id,
						provider=provider,
						consumer=consumer,
						name=name,
						status=status,
					)
				)
	return rows


def layout_swcs_grid(
	swcs: list[str],
	swc_h: dict[str, float],
	swc_w: dict[str, float],
) -> dict[str, tuple[float, float, float, float]]:
	"""Place SWCs on a roughly square grid and return (x, y, w, h) per node.

	Each grid cell is sized to the tallest/widest box in its column/row so
	that boxes with different sizes are centred within their allocated cell
	without overlapping.
	"""
	margin_x, margin_y = 80.0, 80.0
	col_gap, row_gap = 220.0, 100.0
	# Number of columns — rounded square root gives a roughly square grid.
	cols = max(1, math.ceil(math.sqrt(len(swcs))))

	# Split the flat SWC list into grid rows.
	rows_of_swcs: list[list[str]] = []
	for i in range(0, len(swcs), cols):
		rows_of_swcs.append(swcs[i : i + cols])

	# Each column is as wide as its widest member.
	col_widths = [0.0] * cols
	for row in rows_of_swcs:
		for j, s in enumerate(row):
			col_widths[j] = max(col_widths[j], swc_w[s])

	# Each row is as tall as its tallest member.
	row_heights = [max(swc_h[s] for s in row) for row in rows_of_swcs]

	placement: dict[str, tuple[float, float, float, float]] = {}
	y = margin_y
	for ri, row in enumerate(rows_of_swcs):
		x = margin_x
		for j, s in enumerate(row):
			# Centre each box within its cell so it doesn't stick to one corner.
			bx = x + (col_widths[j] - swc_w[s]) / 2.0
			by = y + (row_heights[ri] - swc_h[s]) / 2.0
			placement[s] = (bx, by, swc_w[s], swc_h[s])
			x += col_widths[j] + col_gap
		y += row_heights[ri] + row_gap
	return placement


def center_of(rect: tuple[float, float, float, float]) -> tuple[float, float]:
	"""Return the (cx, cy) centre point of an (x, y, w, h) rectangle."""
	x, y, w, h = rect
	return x + w / 2.0, y + h / 2.0


# Lookup for the face that is geometrically opposite to a given face.
_FACE_OPPOSITE = {"R": "L", "L": "R", "T": "B", "B": "T"}


def face_of(src_c: tuple[float, float], tgt_c: tuple[float, float]) -> str:
	"""Determine which face of src a straight line toward tgt would exit from.

	The dominant axis (larger absolute delta) decides the face: horizontal
	dominance → R or L, vertical dominance → B or T.
	"""
	dx = tgt_c[0] - src_c[0]
	dy = tgt_c[1] - src_c[1]
	if abs(dx) >= abs(dy):
		return "R" if dx >= 0 else "L"
	return "B" if dy >= 0 else "T"


def exit_xy_for(face: str, frac: float) -> tuple[float, float]:
	"""Convert a face name + fraction to a Draw.io (relX, relY) exit point."""
	# frac runs along the face: 0 = top/left edge, 1 = bottom/right edge.
	return {"R": (1.0, frac), "L": (0.0, frac), "B": (frac, 1.0), "T": (frac, 0.0)}[face]


def entry_xy_for(face: str, frac: float) -> tuple[float, float]:
	"""Entry point on the *opposite* face — mirrors exit_xy_for."""
	return exit_xy_for(_FACE_OPPOSITE[face], frac)


def allocate_face_zones(
	neighbor_counts: list[tuple[str, int]],
) -> dict[str, tuple[float, float]]:
	"""Divide the usable face range [0.05, 0.95] proportionally among neighbours.

	Each neighbour receives a contiguous sub-range sized in proportion to its
	interface count.  A small fixed gap (0.02) is left between each zone so
	arrows from different neighbours don't touch each other at the box edge.
	Returns a dict mapping neighbour name → (zone_start, zone_end) in [0, 1].
	"""
	total = sum(c for _, c in neighbor_counts)
	gap = 0.02  # gap between adjacent zones on the same face
	total_gap = gap * (len(neighbor_counts) - 1) if len(neighbor_counts) > 1 else 0.0
	available = 0.90 - total_gap  # usable face length after reserving 5% margins + gaps
	zones: dict[str, tuple[float, float]] = {}
	frac = 0.05  # start at 5% from the top/left edge of the face
	for name, count in neighbor_counts:
		size = available * count / total
		zones[name] = (frac, frac + size)
		frac += size + gap
	return zones


def direction_style(notation: str, color: str) -> str:
	"""Return edge marker style for a given notation and color."""
	if notation == "lollipop":
		# Lollipop/socket symbols are attached separately at edge center.
		return (
			"startArrow=none;"
			"endArrow=none;"
			f"strokeWidth=1.8;html=1;rounded=0;curved=0;strokeColor={color};"
		)
	return (
		"startArrow=none;"
		"endArrow=block;endFill=1;"
		f"strokeWidth=1.6;html=1;rounded=0;curved=0;strokeColor={color};"
	)


def resolve_status_color(status: str, status_colors: dict[str, str] | None = None) -> str:
	"""Return the configured color for status, with a stable unknown fallback."""
	colors = status_colors or _DEFAULT_STATUS_COLORS
	return colors.get(status, colors.get("unknown", _DEFAULT_STATUS_COLORS["unknown"]))


def style_for_status(
	status: str,
	notation: str = "arrow",
	status_colors: dict[str, str] | None = None,
) -> str:
	"""Return a Draw.io edge style string with status color and selected notation."""
	color = resolve_status_color(status, status_colors)
	return (
		direction_style(notation, color)
		+ "labelBackgroundColor=#ffffff;"
	)


def rel_point(rect: tuple[float, float, float, float], rel_xy: tuple[float, float]) -> tuple[float, float]:
	"""Convert Draw.io relative face coordinates (0..1, 0..1) to absolute pixels."""
	x, y, w, h = rect
	return x + rel_xy[0] * w, y + rel_xy[1] * h


def add_center_lollipop(builder: DrawIoBuilder, edge_id: str, angle_deg: float, color: str) -> None:
	"""Attach a lollipop circle and socket symbol to the midpoint of an edge."""
	radians = math.radians(angle_deg)
	ux = math.cos(radians)
	uy = math.sin(radians)
	sep = 4.0
	lp_center = (-sep * ux, -sep * uy)
	sock_center = (sep * ux, sep * uy)
	# Base rotation for a left-to-right edge so the bowl opens toward the lollipop.
	sock_rot = angle_deg + 270.0
	lollipop_d = 12.0
	socket_d = 16.0

	lp_left = lp_center[0] - lollipop_d / 2.0
	lp_top = lp_center[1] - lollipop_d / 2.0
	sock_left = sock_center[0] - socket_d / 2.0
	sock_top = sock_center[1] - socket_d / 2.0

	builder.add_edge_attached_symbol(
		edge_id=edge_id,
		value="",
		style=(
			f"ellipse;aspect=fixed;html=1;strokeColor={color};fillColor=#ffffff;"
			"strokeWidth=1.8;"
		),
		rel_x=0.5,
		offset_x=lp_left,
		offset_y=lp_top,
		w=lollipop_d,
		h=lollipop_d,
	)

	# Draw a round half-socket as a true arc (no masking rectangle).
	builder.add_edge_attached_symbol(
		edge_id=edge_id,
		value="",
		style=(
			"shape=mxgraph.basic.arc;html=1;fillColor=none;"
			f"strokeColor={color};strokeWidth=1.8;rotation={sock_rot:.1f};"
		),
		rel_x=0.5,
		offset_x=sock_left,
		offset_y=sock_top,
		w=socket_d,
		h=socket_d,
	)

def format_interface_label(item: InterfaceRow) -> str:
	"""Build the edge label: "[ID] Name" or just "Name" / "[ID]" as available."""
	label = item.name
	if item.interface_id:
		label = f"[{item.interface_id}] {label}" if label else f"[{item.interface_id}]"
	return label


_PER_IF_PX = 18.0    # pixels allocated per interface on a box face (overview sizing)
_MIN_BOX = 110.0     # minimum box width and height in pixels
_MAX_ASPECT = 3.0    # maximum allowed height/width (or width/height) ratio
_LABEL_SPREAD = 0.55 # maximum total spread of labels along the edge (edge-relative units)
_LABEL_PERP = -8.0   # fixed perpendicular offset: negative = always above the edge


def label_offsets(index: int, count: int) -> tuple[float, float]:
	"""Compute (label_x, label_y) to stagger edge labels so they don't overlap.

	When multiple interfaces share the same pair of SWCs their labels are spread
	along the edge (label_x).  All labels are shifted slightly above the line
	(label_y = _LABEL_PERP) so positioning is consistent and easy to read.
	"""
	if count == 1:
		return 0.0, _LABEL_PERP  # single label: centred along edge, slightly above
	# Clamp spread so it doesn't push labels off the visible part of the edge.
	spread = min(_LABEL_SPREAD, 0.08 * count)
	lx = (index / (count - 1) - 0.5) * spread if count > 1 else 0.0
	return lx, _LABEL_PERP  # always above the line


def _topology_sorted(swcs: list[str], bundles: dict[tuple[str, str], list]) -> list[str]:
	"""Sort SWCs so the most-connected nodes come first (greedy row-fill order).

	Because layout_swcs_grid fills rows left-to-right, placing busy nodes first
	ensures they land in the same row and their connections run mostly horizontally
	rather than diagonally across the diagram.
	"""
	total_if: dict[str, int] = defaultdict(int)
	for (p, c), bundle in bundles.items():
		total_if[p] += len(bundle)
		total_if[c] += len(bundle)
	return sorted(swcs, key=lambda s: -total_if[s])


def _face_if_counts(
	swcs: list[str],
	bundles: dict[tuple[str, str], list[InterfaceRow]],
	rough_layout: dict[str, tuple[float, float, float, float]],
) -> tuple[dict[str, int], dict[str, int]]:
	"""Count how many interfaces land on each box's horizontal vs vertical faces.

	Used in the two-pass sizing step: interfaces on the L/R faces drive the box
	height (more horizontal traffic → taller box); interfaces on the T/B faces
	drive the box width.  A rough equal-size layout is used purely to detect the
	direction between each pair, without needing final coordinates.
	"""
	lr_count: dict[str, int] = defaultdict(int)
	tb_count: dict[str, int] = defaultdict(int)
	for (provider, consumer), bundle in bundles.items():
		src_c = center_of(rough_layout[provider])
		tgt_c = center_of(rough_layout[consumer])
		f = face_of(src_c, tgt_c)
		n = len(bundle)
		if f in ("L", "R"):
			lr_count[provider] += n
			lr_count[consumer] += n
		else:
			tb_count[provider] += n
			tb_count[consumer] += n
	return lr_count, tb_count


def build_diagram(
	rows: list[InterfaceRow],
	notation: str = "arrow",
	status_colors: dict[str, str] | None = None,
	status_map: dict[str, str] | None = None,
) -> str:
	"""Build an overview Draw.io diagram with all SWCs on a grid.

	Two-pass algorithm:
	  1. Place all boxes at equal size to detect which face each connection exits.
	  2. Re-size each box based on actual per-face traffic, then do the final layout.
	"""
	builder = DrawIoBuilder()

	# Collect every unique SWC name that appears as provider or consumer.
	all_swcs = sorted({item.provider for item in rows} | {item.consumer for item in rows})

	# Group all interfaces by (provider, consumer) pair — each group is one bundle.
	bundles: dict[tuple[str, str], list[InterfaceRow]] = defaultdict(list)
	for item in rows:
		bundles[(item.provider, item.consumer)].append(item)

	# Sort by connectivity so the busiest nodes land in the same row.
	swcs = _topology_sorted(all_swcs, bundles)

	# --- Pass 1: rough equal-size layout to detect which face each edge uses --
	rough_w = {s: 200.0 for s in swcs}
	rough_h = {s: 120.0 for s in swcs}
	rough_layout = layout_swcs_grid(swcs, rough_h, rough_w)

	# --- Pass 2: compute face-aware box sizes --------------------------------
	# lr_count = interfaces entering/leaving via L or R face → drives height.
	# tb_count = interfaces entering/leaving via T or B face → drives width.
	lr_count, tb_count = _face_if_counts(swcs, bundles, rough_layout)
	swc_h = {s: max(_MIN_BOX, lr_count.get(s, 1) * _PER_IF_PX + 40.0) for s in swcs}
	swc_w = {s: max(_MIN_BOX, tb_count.get(s, 1) * _PER_IF_PX + 60.0) for s in swcs}

	# Cap aspect ratio so no box becomes an extreme tower or pancake.
	for s in swcs:
		if swc_h[s] > swc_w[s] * _MAX_ASPECT:
			swc_w[s] = swc_h[s] / _MAX_ASPECT
		if swc_w[s] > swc_h[s] * _MAX_ASPECT:
			swc_h[s] = swc_w[s] / _MAX_ASPECT

	# Final grid layout with the face-aware sizes.
	swc_layout = layout_swcs_grid(swcs, swc_h, swc_w)

	# Render SWC boxes; record the Draw.io cell ID for each SWC name.
	swc_style = "rounded=1;whiteSpace=wrap;html=1;strokeWidth=2;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=13;"
	swc_ids: dict[str, str] = {}
	for swc, (x, y, w, h) in swc_layout.items():
		swc_ids[swc] = builder.add_vertex(swc, swc_style, x, y, w, h)

	# --- Pre-compute face zones for every (swc, face) pair -------------------
	# For each face of each SWC, collect all neighbours that connect to it
	# (sorted for determinism) and assign them proportional zone bands.
	face_out_groups: dict[tuple[str, str], list[tuple[str, list[InterfaceRow]]]] = defaultdict(list)
	face_in_groups: dict[tuple[str, str], list[tuple[str, list[InterfaceRow]]]] = defaultdict(list)

	for (provider, consumer), bundle in sorted(bundles.items()):
		src_c = center_of(swc_layout[provider])
		tgt_c = center_of(swc_layout[consumer])
		f = face_of(src_c, tgt_c)
		face_out_groups[(provider, f)].append((consumer, bundle))
		face_in_groups[(consumer, _FACE_OPPOSITE[f])].append((provider, bundle))

	# out_zones key: (swc, exit_face, neighbor) → (zone_start, zone_end)
	out_zones: dict[tuple[str, str, str], tuple[float, float]] = {}
	for (swc, face), nb_list in face_out_groups.items():
		for name, zone in allocate_face_zones([(n, len(b)) for n, b in nb_list]).items():
			out_zones[(swc, face, name)] = zone

	# in_zones key: (swc, entry_face, neighbor) → (zone_start, zone_end)
	in_zones: dict[tuple[str, str, str], tuple[float, float]] = {}
	for (swc, face), nb_list in face_in_groups.items():
		for name, zone in allocate_face_zones([(n, len(b)) for n, b in nb_list]).items():
			in_zones[(swc, face, name)] = zone

	# --- Generate one edge per interface, placed within its zone slot ---------
	for (provider, consumer), bundle in bundles.items():
		src_c = center_of(swc_layout[provider])
		tgt_c = center_of(swc_layout[consumer])
		f = face_of(src_c, tgt_c)
		opp = _FACE_OPPOSITE[f]
		oz = out_zones[(provider, f, consumer)]
		iz = in_zones[(consumer, opp, provider)]
		count = len(bundle)

		for i, item in enumerate(bundle):
			# Distribute the i-th edge evenly across its allocated zone band.
			o_frac = oz[0] + (i + 0.5) * (oz[1] - oz[0]) / count
			i_frac = iz[0] + (i + 0.5) * (iz[1] - iz[0]) / count
			lx, ly = label_offsets(i, count)
			exit_pt = exit_xy_for(f, o_frac)
			entry_pt = entry_xy_for(f, i_frac)
			edge_id = builder.add_edge(
				swc_ids[provider],
				swc_ids[consumer],
				value=format_interface_label(item),
				style=style_for_status(item.status, notation, status_colors),
				exit_xy=exit_pt,
				entry_xy=entry_pt,
				label_x=lx,
				label_y=ly,
			)
			if notation == "lollipop":
				color = resolve_status_color(item.status, status_colors)
				src_pt = rel_point(swc_layout[provider], exit_pt)
				tgt_pt = rel_point(swc_layout[consumer], entry_pt)
				angle_deg = math.degrees(math.atan2(tgt_pt[1] - src_pt[1], tgt_pt[0] - src_pt[0]))
				add_center_lollipop(builder, edge_id, angle_deg, color)

	# Place legend below the bottom-left corner of the diagram.
	max_y = max(rect[1] + rect[3] for rect in swc_layout.values())
	builder.add_legend(x=80.0, y=max_y + 60.0, notation=notation, status_colors=status_colors, status_map=status_map)

	return builder.to_xml_string()


# ── Focus mode layout constants ──────────────────────────────────────────────
# These values control the visual spacing of the focus diagram where one SWC is
# centred and all its providers/consumers are arranged on the left/right sides.
_PER_IF_H = 34.0      # pixels per interface allocated on the focus SWC face
_ZONE_PAD = 12.0      # inner padding at top/bottom of each zone (prevents edge touching)
_ZONE_GAP = 28.0      # vertical gap between neighbour zones on the focus face
_FOCUS_W = 320.0      # width of the centred focus SWC box
_SIDE_W = 250.0       # width of neighbour (provider/consumer) SWC boxes
_SIDE_H_MIN = 70.0    # minimum height for a neighbour SWC box
_SIDE_GAP_X = 300.0   # horizontal gap between focus SWC and its neighbours


def _zone_height(count: int) -> float:
	"""Height in pixels needed on the focus SWC face for a bundle of `count` interfaces."""
	return max(_SIDE_H_MIN, count * _PER_IF_H + 2 * _ZONE_PAD)


def _stack_total(keys: list[str], zone_heights: dict[str, float]) -> float:
	"""Total vertical space occupied by all zones including the gaps between them."""
	if not keys:
		return 0.0
	return sum(zone_heights[k] for k in keys) + _ZONE_GAP * (len(keys) - 1)


def _compute_zone_positions(
	keys: list[str],
	zone_heights: dict[str, float],
	stack_total: float,
	focus_y: float,
	focus_h: float,
) -> dict[str, tuple[float, float]]:
	"""Assign absolute pixel (y_start, y_end) to each zone, centred on the focus SWC.

	Zones are stacked top-to-bottom with _ZONE_GAP between them and the whole
	stack is vertically centred on the focus SWC's bounding box.
	"""
	center = focus_y + focus_h / 2.0
	y = center - stack_total / 2.0  # start from the top of the centred stack
	positions: dict[str, tuple[float, float]] = {}
	for k in keys:
		positions[k] = (y, y + zone_heights[k])
		y += zone_heights[k] + _ZONE_GAP
	return positions


def _focus_fracs(count: int, z0: float, z1: float, focus_y: float, focus_h: float) -> list[float]:
	"""Compute `count` evenly spaced connection-point fractions on the focus SWC face.

	Fractions are relative to the focus SWC height (0 = top, 1 = bottom) and are
	confined within the zone [z0, z1], respecting the inner padding _ZONE_PAD.
	"""
	inner = (z1 - z0) - 2.0 * _ZONE_PAD  # usable height inside zone padding
	inner = max(inner, 0.0)
	fracs = []
	for i in range(count):
		abs_y = z0 + _ZONE_PAD + (i + 0.5) * inner / count
		# Convert absolute pixel position to a 0–1 fraction of the focus SWC height.
		fracs.append(max(0.01, min(0.99, (abs_y - focus_y) / focus_h)))
	return fracs


def _neighbor_fracs(count: int) -> list[float]:
	"""Evenly spread `count` connection-point fractions across a neighbour SWC face.

	Uses the inner 80% of the face (10%–90%) so arrows don't emerge from the corners.
	"""
	if count == 1:
		return [0.5]  # single interface always connects at the box midpoint
	return [0.1 + 0.8 * i / (count - 1) for i in range(count)]


def build_focus_diagram(
	rows: list[InterfaceRow],
	focus_swc: str,
	notation: str = "arrow",
	status_colors: dict[str, str] | None = None,
	status_map: dict[str, str] | None = None,
) -> str:
	"""Build a focus Draw.io diagram centred on a single SWC.

	All providers appear on the left, all consumers on the right.  Each
	neighbour is assigned a dedicated vertical zone on the focus SWC face so
	arrows from different neighbours never overlap.
	"""
	builder = DrawIoBuilder()

	# Split all rows into those where focus_swc receives (incoming) and sends (outgoing).
	incoming = [item for item in rows if item.consumer == focus_swc]
	outgoing = [item for item in rows if item.provider == focus_swc]
	if not incoming and not outgoing:
		raise ValueError(f"Focus SWC '{focus_swc}' has no incoming or outgoing interfaces in the CSV.")

	# Group incoming interfaces by provider and outgoing by consumer.
	incoming_bundles: dict[str, list[InterfaceRow]] = defaultdict(list)
	for item in incoming:
		incoming_bundles[item.provider].append(item)

	outgoing_bundles: dict[str, list[InterfaceRow]] = defaultdict(list)
	for item in outgoing:
		outgoing_bundles[item.consumer].append(item)

	# Alphabetical ordering keeps the diagram stable across regenerations.
	providers_left = sorted(incoming_bundles)
	consumers_right = sorted(outgoing_bundles)

	# Calculate the zone height each neighbour needs on the focus SWC face.
	left_zone_h = {p: _zone_height(len(incoming_bundles[p])) for p in providers_left}
	right_zone_h = {c: _zone_height(len(outgoing_bundles[c])) for c in consumers_right}

	left_total = _stack_total(providers_left, left_zone_h)
	right_total = _stack_total(consumers_right, right_zone_h)

	# The focus SWC must be tall enough to accommodate all zones on either side.
	focus_h = max(180.0, left_total, right_total)
	focus_y = 80.0
	focus_x = 80.0 + _SIDE_W + _SIDE_GAP_X  # centred horizontally with room for providers
	right_x = focus_x + _FOCUS_W + _SIDE_GAP_X  # x-coordinate of the consumer column

	# Compute the absolute pixel positions of each zone on the focus SWC face.
	left_zone_pos = _compute_zone_positions(providers_left, left_zone_h, left_total, focus_y, focus_h)
	right_zone_pos = _compute_zone_positions(consumers_right, right_zone_h, right_total, focus_y, focus_h)

	# Build neighbour SWC bounding boxes — each vertically centred on its zone.
	left_layout: dict[str, tuple[float, float, float, float]] = {}
	for p in providers_left:
		z0, z1 = left_zone_pos[p]
		h = max(_SIDE_H_MIN, z1 - z0)
		left_layout[p] = (80.0, (z0 + z1) / 2.0 - h / 2.0, _SIDE_W, h)

	right_layout: dict[str, tuple[float, float, float, float]] = {}
	for c in consumers_right:
		z0, z1 = right_zone_pos[c]
		h = max(_SIDE_H_MIN, z1 - z0)
		right_layout[c] = (right_x, (z0 + z1) / 2.0 - h / 2.0, _SIDE_W, h)

	# Neighbour boxes use the standard blue SWC style; focus uses gold to stand out.
	swc_style = "rounded=1;whiteSpace=wrap;html=1;strokeWidth=2;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=13;"
	focus_style = "rounded=1;whiteSpace=wrap;html=1;strokeWidth=3;fillColor=#fff2cc;strokeColor=#d6b656;fontSize=15;fontStyle=1;"

	# Render all SWC boxes; the key tuple (name, side) avoids collisions when the
	# same SWC appears on both sides (provides and consumes interfaces with focus).
	swc_ids: dict[tuple[str, str], str] = {}
	for p in providers_left:
		x, y, w, h = left_layout[p]
		swc_ids[(p, "L")] = builder.add_vertex(p, swc_style, x, y, w, h)
	for c in consumers_right:
		x, y, w, h = right_layout[c]
		swc_ids[(c, "R")] = builder.add_vertex(c, swc_style, x, y, w, h)

	focus_id = builder.add_vertex(focus_swc, focus_style, focus_x, focus_y, _FOCUS_W, focus_h)

	# Incoming edges: left neighbour right-face → focus SWC left-face
	for p in providers_left:
		bundle = incoming_bundles[p]
		count = len(bundle)
		z0, z1 = left_zone_pos[p]
		# Fractions on the focus SWC face are confined to this provider's zone.
		f_fracs = _focus_fracs(count, z0, z1, focus_y, focus_h)
		# Fractions on the neighbour face spread across its full usable height.
		n_fracs = _neighbor_fracs(count)
		for i, item in enumerate(bundle):
			lx, ly = label_offsets(i, count)
			exit_pt = (1.0, n_fracs[i])
			entry_pt = (0.0, f_fracs[i])
			edge_id = builder.add_edge(
				swc_ids[(p, "L")],
				focus_id,
				value=format_interface_label(item),
				style=style_for_status(item.status, notation, status_colors),
				exit_xy=exit_pt,   # exit right-face of provider
				entry_xy=entry_pt,  # enter left-face of focus SWC
				label_x=lx,
				label_y=ly,
			)
			if notation == "lollipop":
				color = resolve_status_color(item.status, status_colors)
				src_pt = rel_point(left_layout[p], exit_pt)
				tgt_pt = rel_point((focus_x, focus_y, _FOCUS_W, focus_h), entry_pt)
				angle_deg = math.degrees(math.atan2(tgt_pt[1] - src_pt[1], tgt_pt[0] - src_pt[0]))
				add_center_lollipop(builder, edge_id, angle_deg, color)

	# Outgoing edges: focus SWC right-face → right neighbour left-face
	for c in consumers_right:
		bundle = outgoing_bundles[c]
		count = len(bundle)
		z0, z1 = right_zone_pos[c]
		f_fracs = _focus_fracs(count, z0, z1, focus_y, focus_h)
		n_fracs = _neighbor_fracs(count)
		for i, item in enumerate(bundle):
			lx, ly = label_offsets(i, count)
			exit_pt = (1.0, f_fracs[i])
			entry_pt = (0.0, n_fracs[i])
			edge_id = builder.add_edge(
				focus_id,
				swc_ids[(c, "R")],
				value=format_interface_label(item),
				style=style_for_status(item.status, notation, status_colors),
				exit_xy=exit_pt,   # exit right-face of focus SWC
				entry_xy=entry_pt,  # enter left-face of consumer
				label_x=lx,
				label_y=ly,
			)
			if notation == "lollipop":
				color = resolve_status_color(item.status, status_colors)
				src_pt = rel_point((focus_x, focus_y, _FOCUS_W, focus_h), exit_pt)
				tgt_pt = rel_point(right_layout[c], entry_pt)
				angle_deg = math.degrees(math.atan2(tgt_pt[1] - src_pt[1], tgt_pt[0] - src_pt[0]))
				add_center_lollipop(builder, edge_id, angle_deg, color)

	# Place legend below the focus diagram.
	builder.add_legend(x=80.0, y=focus_y + focus_h + 60.0, notation=notation, status_colors=status_colors, status_map=status_map)

	return builder.to_xml_string()


def main() -> None:
	parser = argparse.ArgumentParser(description="Generate a Draw.io SWC interface diagram from CSV data.")
	parser.add_argument("--input", default="data.csv", help="Input CSV path (semicolon separated).")
	parser.add_argument("--output", default="interface_diagram.drawio", help="Output Draw.io file path.")
	parser.add_argument("--config", default="config.json", help="Path to JSON config file with column mapping.")
	parser.add_argument(
		"--mode",
		choices=["overview", "focus"],
		default="overview",
		help="Diagram mode: overview (all SWCs) or focus (single centered SWC).",
	)
	parser.add_argument(
		"--focus-swc",
		default="",
		help="Name of the focused SWC (required when --mode focus).",
	)
	parser.add_argument(
		"--status",
		choices=["all", "aligned", "draft"],
		default="all",
		help="Filter interfaces by status: all (default), aligned, or draft.",
	)
	parser.add_argument(
		"--notation",
		choices=["arrow", "lollipop"],
		default="arrow",
		help="Edge notation style: arrow (default) or lollipop.",
	)
	args = parser.parse_args()

	input_path = Path(args.input)
	output_path = Path(args.output)

	# Load column mapping, status map, and status colors from config file.
	col_map, status_map, status_colors = load_config(Path(args.config))
	# Build reverse lookup: Excel label (lowercased) → internal key.
	status_reverse_map = {v.lower(): k for k, v in status_map.items()}

	rows = parse_csv(input_path, col_map, status_reverse_map)
	if not rows:
		raise ValueError("No valid interfaces found in input CSV.")

	# Optionally filter to a single status value before building the diagram.
	if args.status != "all":
		rows = [r for r in rows if r.status == args.status]
		if not rows:
			raise ValueError(f"No interfaces with status '{args.status}' found.")

	if args.mode == "focus":
		focus_swc = args.focus_swc.strip()
		if not focus_swc:
			raise ValueError("--focus-swc is required when --mode focus.")
		xml_content = build_focus_diagram(rows, focus_swc, notation=args.notation, status_colors=status_colors, status_map=status_map)
	else:
		xml_content = build_diagram(rows, notation=args.notation, status_colors=status_colors, status_map=status_map)

	output_path.write_text(xml_content, encoding="utf-8")
	print(f"Created Draw.io file: {output_path}")
	print(f"Interfaces rendered: {len(rows)}")


if __name__ == "__main__":
	main()
