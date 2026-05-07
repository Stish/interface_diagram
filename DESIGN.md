# Interface Diagram - Regeneration Design (LLM-Oriented)

## 1. Goal

Recreate a Python CLI tool that reads a semicolon-separated CSV describing software component interfaces and generates a Draw.io `.drawio` XML diagram.

The regenerated tool must support two modes:

1. `overview`: all components arranged on a grid with directional edges.
2. `focus`: one chosen component centered, providers on the left, consumers on the right.

It must also support notation selection (arrow or lollipop), color edges by status, split multi-consumer rows, support configurable CSV headers, and include a legend in the diagram.

## 2. Scope and Non-Goals

### In scope

1. Parse CSV input with configurable column mapping.
2. Normalize rows into one row per `(provider, consumer)` connection.
3. Generate Draw.io XML with vertices and edges.
4. Produce deterministic layout and output ordering.
5. Provide CLI options for mode, focus SWC, status filter, notation, and file paths.

### Out of scope

1. Interactive UI.
2. Editing existing `.drawio` files.
3. Advanced graph optimization (this is heuristic layout, not full graph drawing).

## 3. Runtime and Dependencies

1. Language: Python 3.10+ (type hints with `|` unions).
2. Standard library only:
   - `argparse`
   - `csv`
   - `json`
   - `math`
   - `pathlib`
   - `collections.defaultdict`
   - `dataclasses`
   - `xml.etree.ElementTree`
   - `xml.dom.minidom`

No third-party packages are required.

## 4. Inputs and Outputs

### Input files

1. CSV file (`--input`, default `data.csv`)
   - delimiter: `;`
   - encoding: `utf-8-sig`
2. JSON config (`--config`, default `config.json`)
   - optional header mapping

### Output file

1. Draw.io XML (`--output`, default `interface_diagram.drawio`)
   - UTF-8 text
   - valid Draw.io skeleton (`mxfile -> diagram -> mxGraphModel -> root`)

### Companion post-processing input/output

1. A manually rearranged Draw.io file can be post-processed by a companion utility script.
2. That utility recalculates midpoint lollipop symbol offsets and socket rotation from the current box positions and edge anchors.

## 5. Data Contract

### Internal row model

Use a dataclass:

- `interface_id: str`
- `provider: str`
- `consumer: str`
- `name: str`
- `status: str` (lowercase: `aligned`, `draft`, or empty/unknown)

### Default column mapping

Internal keys -> default CSV header names:

- `id` -> `ID`
- `provider` -> `Component providing`
- `consumer` -> `Component consuming`
- `name` -> `Name`
- `status` -> `Status`

### Config behavior

1. If config file is missing: use default mapping.
2. If config exists: merge `column_map` over defaults.
3. Missing required CSV headers must raise an error.

Required headers: `id`, `provider`, `consumer`, `name` (as mapped).
Status header is optional.

## 6. CSV Parsing Rules

1. Read with `csv.DictReader(delimiter=';')`.
2. For each raw row:
   - trim whitespace on mapped values
   - lowercase `status` when present
3. Skip rows with empty provider.
4. Consumer cell may contain multiple consumers separated by commas.
5. Split consumer list by comma, trim each token, ignore empty tokens.
6. Emit one normalized row per consumer.

This means one CSV row can expand to N interface rows.

## 7. CLI Contract

Arguments:

1. `--input` (default `data.csv`)
2. `--output` (default `interface_diagram.drawio`)
3. `--config` (default `config.json`)
4. `--mode` in `{overview, focus}`, default `overview`
5. `--focus-swc` required if `--mode focus`
6. `--status` in `{all, aligned, draft}`, default `all`
7. `--notation` in `{arrow, lollipop}`, default `arrow`

Behavior:

1. Parse rows.
2. Error if zero valid rows.
3. Apply status filter unless `all`.
4. Error if filter produces zero rows.
5. Build XML based on mode.
6. Apply selected notation style to directional edges and legend.
7. Write output and print:
   - output file path
   - number of interfaces rendered

## 8. Draw.io Builder Requirements

Implement a small builder class with:

1. Auto-incremented cell IDs starting at 2 (`0` and `1` reserved).
2. `add_vertex(...)` for nodes/text/swatch boxes.
3. `add_edge(...)` with optional explicit anchor points:
   - `exitX/exitY`
   - `entryX/entryY`
4. `to_xml_string()` pretty-prints XML with 2-space indentation.
5. `add_legend(x, y, notation)` draws:
   - dedicated legend container box
   - title `Legend`
   - transition direction (Provider -> Consumer) with notation-aware marker style
   - status colors: aligned, draft, unknown

## 9. Style Rules

### SWC nodes

- Rounded rectangle
- Blue palette (`fillColor #dae8fc`, `strokeColor #6c8ebf`)

### Focus node

- Highlighted gold palette (`fillColor #fff2cc`, `strokeColor #d6b656`)

### Edge colors by status

- `aligned` -> dark green `#1a7a1a`
- `draft` -> red `#cc0000`
- otherwise -> gray `#666666`

### Edge notation

- `arrow`: provider -> consumer with a classic filled arrowhead on the consumer side
- `lollipop`: single edge with attached endpoint markers:
   - plain transition line with midpoint-attached symbols
   - circle and round half-socket (geometric half-circle) meet at the center of the transition
   - symbol rotation is computed from the actual slope of the generated edge

In lollipop mode, line and symbols are part of one edge object so moving SWC boxes keeps notation aligned.

### Edge labels

- Format: `[ID] Name`
- If only ID: `[ID]`
- If only name: `Name`

## 10. Overview Mode Algorithm

### 10.1 Build graph bundles

Group normalized rows by `(provider, consumer)` into bundles.
Each interface in a bundle becomes its own edge, but shares routing zone with siblings.

### 10.2 Node ordering

Compute total interface count per SWC (incoming + outgoing) and sort descending.
This stabilizes layout and keeps high-connectivity nodes central.

### 10.3 Two-pass sizing

1. Pass 1 (rough): all boxes equal size, place on grid.
2. Infer face direction for each bundle by comparing source and target centers:
   - if `abs(dx) >= abs(dy)`: horizontal (`R` or `L`)
   - else vertical (`B` or `T`)
3. Count per SWC:
   - LR traffic drives height
   - TB traffic drives width
4. Compute final width/height with minimum and aspect ratio caps.
5. Re-layout grid with final dimensions.

### 10.4 Face zones

For each `(swc, face)`, allocate contiguous zones to neighbors proportionally to bundle size.

Zone allocation constraints:

1. face usable range: `[0.05, 0.95]`
2. fixed gap between neighbor zones: `0.02`
3. zone size proportional to edge count

### 10.5 Edge routing

For each edge index `i` within bundle of size `count`:

1. choose source anchor fraction in provider's zone
2. choose target anchor fraction in consumer's corresponding opposite-face zone
3. add edge with explicit `exit`/`entry` anchors
4. label position:
   - spread along edge (`label_x`) to avoid overlap
   - fixed perpendicular offset above line (`label_y` negative)

### 10.6 Legend placement

Place legend below the lowest node (`max(y+h) + margin`).

## 11. Focus Mode Algorithm

Given `focus_swc`:

1. Incoming set: rows where `consumer == focus_swc`.
2. Outgoing set: rows where `provider == focus_swc`.
3. Error if both empty.
4. Group incoming by provider (left side).
5. Group outgoing by consumer (right side).
6. Sort left and right names alphabetically for deterministic output.

### 11.1 Zone sizing

Each neighbor has a dedicated vertical zone on focus box side:

- zone height = `max(min_side_height, count * per_interface_height + 2 * zone_padding)`

Compute total stacked heights (+ inter-zone gaps) for left and right.
Focus node height is max of left total, right total, and minimum focus height.

### 11.2 Positioning

1. Focus node centered between left and right columns.
2. Left/right node boxes vertically centered on their assigned focus zones.
3. Incoming edges: left neighbor right face -> focus left face.
4. Outgoing edges: focus right face -> right neighbor left face.

### 11.3 Anchor fractions

1. On focus face: anchors are constrained to the neighbor's zone (with inner padding).
2. On neighbor face: anchors spread in inner 80% (`0.1..0.9`) to avoid corners.

### 11.4 Legend placement

Place legend below focus node.

## 12. Determinism Requirements

To support stable regeneration and diff-friendly output:

1. Sort SWC names where possible.
2. Sort bundle iteration or rely on deterministic insertion order from sorted keys.
3. Keep constants centralized.
4. Keep XML attribute generation order stable where practical.

## 13. Constants (Expected Tuning Points)

Overview-related constants:

1. per-interface pixels
2. minimum box size
3. max aspect ratio
4. label spread and label perpendicular offset

Focus-related constants:

1. per-interface zone height
2. zone padding
3. zone gap
4. focus width
5. side width and min height
6. horizontal column gap

Legend dimensions should remain explicit constants.

## 14. Error Handling

Raise `ValueError` for:

1. Missing required CSV columns.
2. No valid interfaces after parsing.
3. No rows after status filtering.
4. `focus` mode without `--focus-swc`.
5. Focus SWC not connected in filtered data.

## 15. Regeneration Checklist for an LLM

When regenerating, ensure all items pass:

1. Supports both `overview` and `focus` modes.
2. Splits comma-separated consumers into distinct edges.
3. Applies status colors exactly.
4. Supports optional status filtering at CLI level.
5. Supports notation switching (`arrow`, `lollipop`) via CLI.
6. Uses configurable column mapping from JSON.
7. Builds valid Draw.io XML skeleton with required root cells `0` and `1`.
8. Includes legend with direction and color semantics.
9. Uses deterministic ordering.
10. Handles missing/invalid conditions with clear `ValueError` messages.
11. Writes UTF-8 output and prints summary lines.

## 16. Minimal Acceptance Tests

### Parse tests

1. Row with `consumer = "A, B"` becomes 2 normalized rows.
2. Missing provider row is skipped.
3. Missing required header raises error.

### Filter tests

1. `--status aligned` keeps only aligned rows.
2. Empty result after filter raises error.

### Focus tests

1. Missing `--focus-swc` in focus mode raises error.
2. Unknown focus SWC raises error.
3. Incoming/outgoing edges use left/right routing logic.

### Output tests

1. XML contains mandatory Draw.io root structure.
2. Node and edge counts are consistent with normalized rows.
3. Legend elements are present.
4. Edge label format matches `[ID] Name` rule.
5. `--notation lollipop` output contains lollipop/socket endpoint markers.
6. `--notation arrow` output contains classic arrow endpoint markers.

## 17. Suggested File Structure

1. `interface_diagram.py` (single-file implementation is acceptable)
2. `config.json` (column mapping)
3. `data.csv` (sample data)
4. `DESIGN.md` (this document)
5. `sync_lollipop_geometry.py` (optional post-processing tool for manually rearranged Draw.io files)

Optional refactor target:

1. `parser.py`
2. `layout.py`
3. `drawio_builder.py`
4. `cli.py`

## 18. Regeneration Prompt Template

Use this prompt with an LLM to rebuild the project:

"Implement a Python 3.10+ CLI tool that reads a semicolon CSV of software component interfaces and writes a Draw.io XML diagram. Include two modes: overview (all components on grid with face-aware edge routing) and focus (single centered component with providers left and consumers right). Use standard library only. Add JSON-configurable CSV header mapping, status-based edge coloring (aligned green, draft red, unknown gray), notation selection (`--notation arrow|lollipop`) where lollipop uses provider-side circle and consumer-side socket marker, comma-splitting for multi-consumer cells, deterministic ordering, and a notation-aware legend. Enforce the CLI contract and error conditions from DESIGN.md. Output must be valid Draw.io XML with required root cells id=0 and id=1."