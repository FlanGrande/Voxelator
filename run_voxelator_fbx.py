#!/usr/bin/env python3
"""Headless runner: import one FBX and export one voxel slices PNG.

Usage:
  blender -b -P run_voxelator_fbx.py -- \
    --fbx "/path/model.fbx" \
    --out "/path/output.png" \
    --res 64 --fill 0 --separate 0
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
    bpy.ops.import_scene.fbx(filepath=fbx_path)
    after = set(bpy.data.objects.keys())
    new_names = sorted(after - before)
    return [bpy.data.objects[name] for name in new_names]


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


def main():
    parser = argparse.ArgumentParser(description="Import one FBX and run Voxelator once")
    parser.add_argument("--fbx", required=True, help="Input FBX path")
    parser.add_argument("--out", required=True, help="Output PNG path")
    parser.add_argument("--res", type=int, default=64, help="Voxel resolution (default: 64)")
    parser.add_argument("--fill", type=int, choices=(0, 1), default=0, help="Fill volume (0/1)")
    parser.add_argument("--separate", type=int, choices=(0, 1), default=0, help="Separate cubes (0/1)")
    parser.add_argument("--log", default="", help="Optional log file path")
    args = parser.parse_args(_script_args(sys.argv))

    script_dir = os.path.dirname(os.path.abspath(__file__))
    fbx_path = os.path.abspath(args.fbx)
    out_path = _ensure_output_png(os.path.abspath(args.out))
    log_path = os.path.abspath(args.log) if args.log else ""

    if not os.path.isfile(fbx_path):
        print(f"ERROR: FBX not found: {fbx_path}")
        return 2

    _load_voxelator_operator(script_dir)
    _clear_scene_objects()

    imported_objects = _import_fbx_and_get_new_objects(fbx_path)
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
    print(f"Output PNG: {out_path}")

    result = bpy.ops.object.voxelize(
        "EXEC_DEFAULT",
        voxelizeResolution=max(1, int(args.res)),
        fill_volume=bool(args.fill),
        separate_cubes=bool(args.separate),
        slices_only=True,
        export_animation=False,
        slices_filepath=out_path,
        log_filepath=log_path,
    )

    if "FINISHED" not in result:
        print(f"ERROR: Voxelator failed: {result}")
        return 4

    print("Voxelator completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
