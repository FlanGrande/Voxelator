# Voxelator

Voxelator turns Blender mesh objects into voxel slice PNGs and optional voxel meshes.

This fork also includes command-line helpers for importing `.fbx` files, exporting one or all animations, processing folders in batch, and stacking generated PNGs into one vertical spritesheet.

## Requirements

- Blender `4.5.1` or newer recommended.
- Python available on your PATH for helper scripts.
- Pillow for `make_vertical_spritesheet.py`:

```bash
python -m pip install pillow
```

- GNU `parallel` only if you use `remake_men_and_women.sh`.

## Files

- `voxelator.py` - Blender add-on and voxelization operator.
- `run_voxelator_fbx.py` - headless Blender runner for one `.fbx` file.
- `run_voxelator_batch.py` - recursive batch runner for folders of `.fbx` files.
- `make_vertical_spritesheet.py` - stacks PNG files vertically.
- `remake_men_and_women.sh` - hardcoded local batch example for modular character folders.

## Blender Add-On Usage

1. Open Blender.
2. Go to `Edit > Preferences > Add-ons > Install`.
3. Select `voxelator.py`.
4. Enable the Voxelator add-on.
5. Select one mesh object in the 3D View.
6. Run `Object > Voxelate`.

### Add-On Options

- `Voxel Resolution` - number of cells on the longest mesh axis. Higher values create more detail but take much longer.
- `Fill Volume` - fills interior voxels instead of only surface voxels.
- `Separate Cubes` - keeps cubes split instead of sharing vertices inside one mesh.
- `Rotation Offset Z` - rotates the model around Z before voxelization.
- `Animation` - selects an action from the current Blender file.
- `Export Animation` - exports selected animation frames to one stacked PNG.
- `Frame Step` - samples every Nth animation frame.
- `Slices Only` - exports PNG slices without building the voxel mesh.
- `Slices PNG` - output path for generated PNG.
- `Log File` - output path for processing log.

## Output Format

Static exports create one PNG spritesheet:

- Width = `tile_size * z_slices`.
- Height = `tile_size`.
- Each horizontal tile is one Z slice.
- Transparent pixels mean empty cells.

Animation exports create one PNG spritesheet:

- Width = `tile_size * z_slices`.
- Height = `tile_size * frame_count`.
- Each row is one sampled animation frame.
- Each row contains all Z slices left to right.

## Single FBX Export

Use Blender in background mode with `run_voxelator_fbx.py`:

```bash
blender -b -P run_voxelator_fbx.py -- \
  --fbx "/path/to/model.fbx" \
  --out "model_all.png" \
  --res 64 \
  --fill 0 \
  --separate 0
```

If `--out` is only a filename, output is written next to the FBX file. If omitted, output defaults to `<fbx-name>.png` next to the FBX.

### Export One Animation

```bash
blender -b -P run_voxelator_fbx.py -- \
  --fbx "/path/to/character.fbx" \
  --out "character_run.png" \
  --res 64 \
  --export-animation 1 \
  --action "Run" \
  --frame-step 2
```

### Export All Imported Animations

```bash
blender -b -P run_voxelator_fbx.py -- \
  --fbx "/path/to/character.fbx" \
  --out "character_all.png" \
  --res 64 \
  --export-animation 1 \
  --action All \
  --frame-step 2
```

When exporting multiple actions, output names become:

```text
character_all__ActionName.png
```

### Single Runner Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--fbx` | required | Input FBX path. |
| `--out` | FBX folder/name | Output PNG path or filename. |
| `--res` | `64` | Voxel resolution on longest axis. |
| `--fill` | `0` | `1` fills interior volume. |
| `--separate` | `0` | `1` separates cube geometry. |
| `--rot-offset` | `0.0` | Z rotation offset in degrees. |
| `--export-animation` | `0` | `1` exports animation spritesheet. |
| `--action` | `DefaultPose` | Action name, or `All`. |
| `--frame-step` | `1` | Sample every Nth frame. |
| `--log` | beside output | Log file path or filename. |

## Batch FBX Export

Use `run_voxelator_batch.py` to recursively process every `.fbx` under a folder:

```bash
python run_voxelator_batch.py \
  --input-dir "/path/to/fbx-folder" \
  --blender blender \
  --res 64 \
  --export-animation 1 \
  --action All \
  --frame-step 2
```

The batch runner calls Blender once per FBX and writes logs next to each input file.

### Useful Batch Commands

Preview files without processing:

```bash
python run_voxelator_batch.py --input-dir "/path/to/fbx-folder" --dry-run
```

Skip FBX files that already have generated PNGs:

```bash
python run_voxelator_batch.py --input-dir "/path/to/fbx-folder" --skip-existing
```

Delete old generated PNG/log outputs before processing:

```bash
python run_voxelator_batch.py --input-dir "/path/to/fbx-folder" --clean-output
```

Limit test run to first 5 FBX files:

```bash
python run_voxelator_batch.py --input-dir "/path/to/fbx-folder" --max-files 5
```

### Batch Runner Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--input-dir` | required | Root folder scanned recursively for `.fbx`. |
| `--blender` | `blender` | Blender executable path. |
| `--runner` | sibling `run_voxelator_fbx.py` | Single-FBX runner path. |
| `--res` | `64` | Voxel resolution. |
| `--fill` | `0` | `1` fills interior volume. |
| `--separate` | `0` | `1` separates cube geometry. |
| `--rot-offset` | `0.0` | Z rotation offset in degrees. |
| `--export-animation` | `0` | `1` exports animations. |
| `--action` | `All` | Action name, or `All`. |
| `--frame-step` | `1` | Sample every Nth animation frame. |
| `--skip-existing` | off | Skip FBX files with existing output PNGs. |
| `--max-files` | `0` | Optional cap. `0` means no cap. |
| `--dry-run` | off | List discovered FBX files and exit. |
| `--clean-output` | off | Remove generated outputs before processing. |
| `--report-path` | input folder | Report file or report directory path. |
| `--python-site` | `~/.local/lib/python3.14/site-packages` | Extra site-packages path for Blender subprocesses. |

## Batch Reports And Logs

Batch runs create:

- `<fbx-stem>_all.batch.log` next to each FBX.
- `<fbx-stem>_all.log` from Voxelator.
- `voxelator_batch_report_<timestamp>.txt` summary report.
- `voxelator_batch_report_<timestamp>.json` machine-readable report.

The batch command returns non-zero if any FBX fails.

## Vertical Spritesheet Helper

Use `make_vertical_spritesheet.py` to stack PNGs top-to-bottom in natural filename order:

```bash
python make_vertical_spritesheet.py "character_all__*.png" -o character_combined.png
```

Input images are converted to RGBA. Output width is the widest input image. Output height is the sum of all input image heights.

## Hardcoded Local Batch Script

`remake_men_and_women.sh` is a local convenience script. It assumes these exact folders exist:

- `Ultimate Modular Men Pack-zip`
- `Ultimate Modular Women Pack-zip`

It runs many character subfolders in parallel with:

```bash
bash remake_men_and_women.sh
```

Edit paths and `--jobs` before using it on another machine.

## Notes

- Use uniform object scale before voxelizing. Apply scale in Blender if needed.
- High `--res` values can be slow and produce huge PNGs.
- For texture-like flat colors, use closest/nearest image sampling in Blender materials.
- Rotated meshes are supported through `--rot-offset`, but complex rotations may need testing.
