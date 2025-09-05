## Visualizing Required Outputs

This CLI renders the PRD v2 required output CSVs under `out/` into quick-look figures saved to `out/viz/`.

### Usage

- All figures
  - `python viz_required_outputs.py all`

- Individual plots
  - Operation timeline (Gantt): `python viz_required_outputs.py op`
  - op_state timeline (Gantt): `python viz_required_outputs.py state`
  - Address touch heatmap: `python viz_required_outputs.py heatmap --kinds PROGRAM READ`
  - State × Operation × Input-time histogram: `python viz_required_outputs.py hist --topk-states 6 --topk-ops 4`

Options
- `--out-dir`: CSV directory (default: `out`)
- `--save-dir`: where to save PNGs (default: `out/viz`)
- `--no-save`: don’t save; show interactively instead

### File Expectations

The tool auto-picks the latest matching CSVs in `out/` by prefix:
- `operation_timeline_*.csv`
- `op_state_timeline_*.csv`
- `address_touch_count_*.csv`
- `op_state_name_input_time_count_*.csv`

If a file is missing, the corresponding plot is skipped with a log message.

