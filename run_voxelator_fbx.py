#!/usr/bin/env python3
"""Headless runner: import one FBX and export voxel slices PNG(s).

Usage:
  blender -b -P run_voxelator_fbx.py -- \
    --fbx "/path/model.fbx" \
    --out "/path/output.png" \
    --res 64 --fill 0 --separate 0 \
    --export-animation 1 --action "All" --frame-step 2
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys

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
        "slices_only": True,
        "export_animation": bool(export_animation),
        "frame_step": max(1, int(args.frame_step)),
        "slices_filepath": out_path,
        "log_filepath": args.log_path,
    }
    if action_name and action_name in bpy.data.actions.keys():
        op_args["animation_action"] = action_name

    result = bpy.ops.object.voxelize("EXEC_DEFAULT", **op_args)
    return result


def main():
    parser = argparse.ArgumentParser(description="Import one FBX and run Voxelator")
    parser.add_argument("--fbx", required=True, help="Input FBX path")
    parser.add_argument("--out", required=True, help="Output PNG path")
    parser.add_argument("--res", type=int, default=64, help="Voxel resolution (default: 64)")
    parser.add_argument("--fill", type=int, choices=(0, 1), default=0, help="Fill volume (0/1)")
    parser.add_argument("--separate", type=int, choices=(0, 1), default=0, help="Separate cubes (0/1)")
    parser.add_argument("--export-animation", type=int, choices=(0, 1), default=0, help="Export animation mode (0/1)")
    parser.add_argument("--action", default="DefaultPose", help="Action name or 'All' for all detected FBX actions")
    parser.add_argument("--frame-step", type=int, default=1, help="Frame step for animation export (default: 1)")
    parser.add_argument("--log", default="", help="Optional log file path")
    args = parser.parse_args(_script_args(sys.argv))

    script_dir = os.path.dirname(os.path.abspath(__file__))
    fbx_path = os.path.abspath(args.fbx)
    out_path = _ensure_output_png(os.path.abspath(args.out))
    log_path = os.path.abspath(args.log) if args.log else ""
    args.log_path = log_path

    if not os.path.isfile(fbx_path):
        print(f"ERROR: FBX not found: {fbx_path}")
        return 2

    _load_voxelator_operator(script_dir)
    _clear_scene_objects()

    imported_objects, imported_actions = _import_fbx_and_get_new_objects(fbx_path)
    mesh_objects = [o for o in imported_objects if o.type == "MESH"]
    if not mesh_objects:
        print("ERROR: No mesh objects found after FBX import")
        return 3

    joined_mesh = _join_meshes(mesh_objects)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    joined_mesh.select_set(True)
    bpy.context.view_layer.objects.active = joined_mesh

    print(f"Imported FBX: {fbx_path}")
    print(f"Using mesh: {joined_mesh.name}")
    print(f"Base Output PNG: {out_path}")
    if imported_actions:
        print(f"Detected imported actions ({len(imported_actions)}):")
        for action in sorted(imported_actions, key=lambda a: a.name):
            print(f"  - {action.name}")
    else:
        print("Detected imported actions: 0")

    if not bool(args.export_animation):
        result = _run_voxelize(joined_mesh, out_path, args, export_animation=False, action_name="NONE")
        if "FINISHED" not in result:
            print(f"ERROR: Voxelator failed: {result}")
            return 4
        print(f"Voxelator completed successfully: {out_path}")
        return 0

    if args.action.lower() == "all":
        actions_to_run = sorted(imported_actions, key=lambda a: a.name)
        if not actions_to_run:
            print("ERROR: --action All requested, but no actions were imported from FBX")
            return 5
    else:
        selected = bpy.data.actions.get(args.action)
        if not selected:
            print(f"ERROR: action not found: {args.action}")
            return 6
        actions_to_run = [selected]

    success_paths = []
    failures = []
    for action in actions_to_run:
        action_out = _out_path_for_action(out_path, action.name) if len(actions_to_run) > 1 else out_path
        print(f"Exporting action '{action.name}' -> {action_out}")
        result = _run_voxelize(joined_mesh, action_out, args, export_animation=True, action_name=action.name)
        if "FINISHED" in result:
            success_paths.append(action_out)
        else:
            failures.append((action.name, str(result)))
            print(f"WARNING: failed action '{action.name}': {result}")

    print(f"Export summary: {len(success_paths)} success, {len(failures)} failed")
    for path in success_paths:
        print(f"  OK  {path}")
    for name, err in failures:
        print(f"  ERR {name}: {err}")

    if not success_paths:
        return 7
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
