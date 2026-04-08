#!/usr/bin/env python3
"""Headless runner: import one FBX and export voxel slices PNG(s).

Usage:
  blender -b -P run_voxelator_fbx.py -- \
    --fbx "/path/model.fbx" \
    --out "output.png" \
    --res 64 --fill 0 --separate 0 \
    --export-animation 1 --action "All" --frame-step 2
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

import bpy


def _script_args(argv):
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return []


def _load_voxelator_operator(script_dir):
    module_path = os.path.join(script_dir, "voxelator.py")
    if not os.path.isfile(module_path):
        raise FileNotFoundError(f"voxelator.py not found at: {module_path}")

    spec = importlib.util.spec_from_file_location("voxelator_cli_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        module.register()
    except Exception:
        pass

    if not hasattr(bpy.ops.object, "voxelize"):
        raise RuntimeError("Voxelator operator object.voxelize is not available")


def _clear_scene_objects():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _import_fbx_and_get_new_objects(fbx_path):
    before = set(bpy.data.objects.keys())
    before_actions = set(bpy.data.actions.keys())
    bpy.ops.import_scene.fbx(filepath=fbx_path)
    after = set(bpy.data.objects.keys())
    after_actions = set(bpy.data.actions.keys())
    new_names = sorted(after - before)
    new_action_names = sorted(after_actions - before_actions)
    return [bpy.data.objects[name] for name in new_names], [bpy.data.actions[name] for name in new_action_names]


def _largest_mesh(mesh_objects):
    def volume_like(obj):
        d = obj.dimensions
        return d.x * d.y * d.z

    return max(mesh_objects, key=volume_like)


def _join_meshes(mesh_objects):
    if len(mesh_objects) == 1:
        return mesh_objects[0]

    active = _largest_mesh(mesh_objects)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)

    for obj in mesh_objects:
        obj.select_set(True)

    bpy.context.view_layer.objects.active = active
    bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def _ensure_output_png(path):
    if not path.lower().endswith(".png"):
        return path + ".png"
    return path


def _resolve_output_path(fbx_path, out_arg):
    fbx_dir = os.path.dirname(os.path.abspath(fbx_path))
    if not out_arg:
        base = os.path.splitext(os.path.basename(fbx_path))[0]
        return _ensure_output_png(os.path.join(fbx_dir, base + ".png"))

    out_arg = out_arg.strip()
    if os.path.dirname(out_arg):
        return _ensure_output_png(os.path.abspath(out_arg))

    return _ensure_output_png(os.path.join(fbx_dir, out_arg))


def _resolve_log_path(fbx_path, log_arg, out_path):
    fbx_dir = os.path.dirname(os.path.abspath(fbx_path))
    if not log_arg:
        base, _ = os.path.splitext(out_path)
        return base + ".log"

    log_arg = log_arg.strip()
    if not log_arg.lower().endswith(".log"):
        log_arg = log_arg + ".log"

    if os.path.dirname(log_arg):
        return os.path.abspath(log_arg)

    return os.path.join(fbx_dir, log_arg)


def _sanitize_name(text):
    safe = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_"):
            safe.append(ch)
        else:
            safe.append("_")
    out = "".join(safe).strip("_")
    return out or "action"


def _out_path_for_action(base_out_path, action_name):
    root, ext = os.path.splitext(base_out_path)
    return f"{root}__{_sanitize_name(action_name)}{ext}"


def _run_voxelize(mesh_obj, out_path, args, export_animation=False, action_name="NONE"):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj

    op_args = {
        "voxelizeResolution": max(1, int(args.res)),
        "fill_volume": bool(args.fill),
        "separate_cubes": bool(args.separate),
        "rotation_offset_deg": float(args.rot_offset),
        "slices_only": True,
        "export_animation": bool(export_animation),
        "frame_step": max(1, int(args.frame_step)),
        "slices_filepath": out_path,
        "log_filepath": args.log_path,
        "console_progress": True,
    }
    if action_name and action_name in bpy.data.actions.keys():
        op_args["animation_action"] = action_name

    result = bpy.ops.object.voxelize("EXEC_DEFAULT", **op_args)
    return result


def _print_result(success, exported, failed, mode, outputs, error=""):
    payload = {
        "success": bool(success),
        "exported": int(exported),
        "failed": int(failed),
        "mode": str(mode),
        "outputs": list(outputs),
        "error": str(error),
    }
    print("VOXELATOR_RESULT " + json.dumps(payload, ensure_ascii=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Import one FBX and run Voxelator")
    parser.add_argument("--fbx", required=True, help="Input FBX path")
    parser.add_argument("--out", default="", help="Output PNG path or filename (default: FBX folder)")
    parser.add_argument("--res", type=int, default=64, help="Voxel resolution (default: 64)")
    parser.add_argument("--fill", type=int, choices=(0, 1), default=0, help="Fill volume (0/1)")
    parser.add_argument("--separate", type=int, choices=(0, 1), default=0, help="Separate cubes (0/1)")
    parser.add_argument("--rot-offset", type=float, default=0.0, help="Z rotation offset in degrees (default: 0)")
    parser.add_argument("--export-animation", type=int, choices=(0, 1), default=0, help="Export animation mode (0/1)")
    parser.add_argument("--action", default="DefaultPose", help="Action name or 'All' for all detected FBX actions")
    parser.add_argument("--frame-step", type=int, default=1, help="Frame step for animation export (default: 1)")
    parser.add_argument("--log", default="", help="Optional log file path or filename (default: alongside output)")
    args = parser.parse_args(_script_args(sys.argv))

    script_dir = os.path.dirname(os.path.abspath(__file__))
    fbx_path = os.path.abspath(args.fbx)
    out_path = _resolve_output_path(fbx_path, args.out)
    log_path = _resolve_log_path(fbx_path, args.log, out_path)
    args.log_path = log_path

    if not os.path.isfile(fbx_path):
        print(f"ERROR: FBX not found: {fbx_path}")
        _print_result(False, 0, 0, "init", [], error=f"fbx_not_found:{fbx_path}")
        return 2

    _load_voxelator_operator(script_dir)
    _clear_scene_objects()

    imported_objects, imported_actions = _import_fbx_and_get_new_objects(fbx_path)
    mesh_objects = [o for o in imported_objects if o.type == "MESH"]
    if not mesh_objects:
        print("ERROR: No mesh objects found after FBX import")
        _print_result(False, 0, 0, "import", [], error="no_mesh_objects")
        return 3

    joined_mesh = _join_meshes(mesh_objects)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    joined_mesh.select_set(True)
    bpy.context.view_layer.objects.active = joined_mesh

    print(f"Imported FBX: {fbx_path}")
    print(f"Using mesh: {joined_mesh.name}")
    print(f"Base Output PNG: {out_path}")
    print(f"Log File: {log_path}")
    if imported_actions:
        print(f"Detected imported actions ({len(imported_actions)}):")
        for action in sorted(imported_actions, key=lambda a: a.name):
            print(f"  - {action.name}")
    else:
        print("Detected imported actions: 0")

    if not bool(args.export_animation):
        t0 = time.perf_counter()
        print("[Voxelator CLI] Starting single-frame export...", flush=True)
        result = _run_voxelize(joined_mesh, out_path, args, export_animation=False, action_name="NONE")
        if "FINISHED" not in result:
            print(f"ERROR: Voxelator failed: {result}")
            _print_result(False, 0, 1, "single", [], error=f"operator_failed:{result}")
            return 4
        dt = time.perf_counter() - t0
        print(f"[Voxelator CLI] Completed in {dt:.2f}s: {out_path}")
        _print_result(True, 1, 0, "single", [out_path])
        return 0

    if args.action.lower() == "all":
        actions_to_run = sorted(imported_actions, key=lambda a: a.name)
        if not actions_to_run:
            print("ERROR: --action All requested, but no actions were imported from FBX")
            _print_result(False, 0, 0, "animation", [], error="no_imported_actions")
            return 5
    else:
        selected = bpy.data.actions.get(args.action)
        if not selected:
            print(f"ERROR: action not found: {args.action}")
            _print_result(False, 0, 0, "animation", [], error=f"action_not_found:{args.action}")
            return 6
        actions_to_run = [selected]

    success_paths = []
    failures = []
    total_actions = len(actions_to_run)
    for idx, action in enumerate(actions_to_run, start=1):
        action_out = _out_path_for_action(out_path, action.name) if len(actions_to_run) > 1 else out_path
        print(f"[Voxelator CLI] Action {idx}/{total_actions}: '{action.name}'", flush=True)
        print(f"[Voxelator CLI] Output: {action_out}", flush=True)
        t0 = time.perf_counter()
        result = _run_voxelize(joined_mesh, action_out, args, export_animation=True, action_name=action.name)
        if "FINISHED" in result:
            dt = time.perf_counter() - t0
            print(f"[Voxelator CLI] Finished '{action.name}' in {dt:.2f}s", flush=True)
            success_paths.append(action_out)
        else:
            failures.append((action.name, str(result)))
            print(f"WARNING: failed action '{action.name}': {result}")

    print(f"Export summary: {len(success_paths)} success, {len(failures)} failed")
    for path in success_paths:
        print(f"  OK  {path}")
    for name, err in failures:
        print(f"  ERR {name}: {err}")

    _print_result(bool(success_paths), len(success_paths), len(failures), "animation", success_paths, error="" if success_paths else "no_successful_actions")

    if not success_paths:
        return 7
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
