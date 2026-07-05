#!/usr/bin/env python3
"""Preview a Voxelator stacked PNG as a rotatable voxel mesh in Blender."""

from __future__ import annotations

import argparse
import os
import sys

import bpy
from mathutils import Vector


def _script_args(argv):
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return []


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _load_pixels(path):
    image = bpy.data.images.load(path, check_existing=True)
    width, height = image.size
    pixels = list(image.pixels[:])
    return image, int(width), int(height), pixels


def _rgba_at(pixels, width, height, x, y):
    idx = ((height - 1 - y) * width + x) * 4
    return (
        float(pixels[idx]),
        float(pixels[idx + 1]),
        float(pixels[idx + 2]),
        float(pixels[idx + 3]),
    )


def _cells_from_stacked_png(pixels, width, height, dx, dy, dz, tile_size, alpha_threshold):
    tile = max(1, int(tile_size))
    off_x = (tile - dx) // 2
    off_y = (tile - dy) // 2
    cells = {}
    for iz in range(dz):
        x0 = iz * tile
        for ix in range(dx):
            px = x0 + off_x + ix
            if px < 0 or px >= width:
                continue
            for iy in range(dy):
                py = off_y + iy
                if py < 0 or py >= height:
                    continue
                color = _rgba_at(pixels, width, height, px, py)
                if color[3] > alpha_threshold:
                    cells[(ix, iy, iz)] = color
    return cells


def _build_voxel_mesh_data(cells, dx, dy, dz):
    face_defs = (
        ((1, 0, 0), ((1, -1, -1), (1, -1, 1), (1, 1, 1), (1, 1, -1))),
        ((-1, 0, 0), ((-1, -1, -1), (-1, 1, -1), (-1, 1, 1), (-1, -1, 1))),
        ((0, 1, 0), ((-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1))),
        ((0, -1, 0), ((-1, -1, -1), (-1, -1, 1), (1, -1, 1), (1, -1, -1))),
        ((0, 0, 1), ((-1, -1, 1), (-1, 1, 1), (1, 1, 1), (1, -1, 1))),
        ((0, 0, -1), ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1))),
    )

    occupied = set(cells)
    verts = []
    faces = []
    face_cells = []
    vert_map = {}
    half = 0.5
    ox = -((dx - 1) * 0.5)
    oy = -((dy - 1) * 0.5)
    oz = -((dz - 1) * 0.5)

    for cell in sorted(occupied):
        ix, iy, iz = cell
        for normal, corners in face_defs:
            nx, ny, nz = normal
            if (ix + nx, iy + ny, iz + nz) in occupied:
                continue

            face = []
            for sx, sy, sz in corners:
                lx = 2 * ix + sx
                ly = 2 * iy + sy
                lz = 2 * iz + sz
                key = (lx, ly, lz)
                vi = vert_map.get(key)
                if vi is None:
                    vi = len(verts)
                    verts.append((ox + lx * half, oy + ly * half, oz + lz * half))
                    vert_map[key] = vi
                face.append(vi)
            faces.append(face)
            face_cells.append(cell)

    return verts, faces, face_cells


def _make_color_material():
    mat = bpy.data.materials.new("VoxelatorPreview_VoxelColor")
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        attr = nodes.new(type="ShaderNodeAttribute")
        attr.attribute_name = "VoxelColor"
        if "Color" in attr.outputs and "Base Color" in bsdf.inputs:
            mat.node_tree.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        if "Alpha" in attr.outputs and "Alpha" in bsdf.inputs:
            mat.node_tree.links.new(attr.outputs["Alpha"], bsdf.inputs["Alpha"])
            mat.blend_method = "BLEND"
    return mat


def _apply_colors(obj, face_cells, cells):
    mesh = obj.data
    color_attr = mesh.color_attributes.new(name="VoxelColor", type="BYTE_COLOR", domain="CORNER")
    flat = [1.0] * (len(mesh.loops) * 4)
    loop_starts = [0] * len(mesh.polygons)
    loop_totals = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get("loop_start", loop_starts)
    mesh.polygons.foreach_get("loop_total", loop_totals)
    for pi, cell in enumerate(face_cells):
        color = cells.get(cell, (1.0, 1.0, 1.0, 1.0))
        for li in range(loop_starts[pi], loop_starts[pi] + loop_totals[pi]):
            base = li * 4
            flat[base] = color[0]
            flat[base + 1] = color[1]
            flat[base + 2] = color[2]
            flat[base + 3] = color[3]
    color_attr.data.foreach_set("color", flat)
    mesh.materials.append(_make_color_material())
    mesh.update()


def _setup_scene(obj, source_image_name):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    max_dim = max(obj.dimensions) if obj.dimensions else 1.0
    distance = max(8.0, max_dim * 2.2)
    bpy.ops.object.light_add(type="AREA", location=(0.0, -distance * 0.8, distance))
    light = bpy.context.object
    light.name = "Voxelator Preview Light"
    light.data.energy = 500.0
    light.data.size = max(5.0, max_dim)

    bpy.ops.object.camera_add(location=(distance, -distance, distance * 0.75), rotation=(1.1, 0.0, 0.785398))
    cam = bpy.context.object
    bpy.context.scene.camera = cam
    try:
        direction = Vector(obj.location) - Vector(cam.location)
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    except Exception:
        pass

    try:
        bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        try:
            bpy.context.scene.render.engine = "BLENDER_EEVEE"
        except Exception:
            pass
    bpy.context.scene.view_settings.view_transform = "Standard"
    bpy.context.scene.world.color = (0.04, 0.04, 0.04)

    screen = getattr(bpy.context, "screen", None)
    if not screen:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if not region:
                continue
            override = {"area": area, "region": region, "edit_object": None, "active_object": obj, "selected_objects": [obj]}
            with bpy.context.temp_override(**override):
                bpy.ops.view3d.view_axis(type="FRONT", align_active=False)
                bpy.ops.view3d.view_selected(use_all_regions=False)
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.shading.type = "MATERIAL"
                    space.overlay.show_floor = True
            break

    bpy.context.scene.name = f"Voxelator Preview - {source_image_name}"


def main():
    parser = argparse.ArgumentParser(description="Preview Voxelator stacked PNG as a rotatable mesh")
    parser.add_argument("--png", required=True, help="Stacked PNG produced by Voxelator")
    parser.add_argument("--dx", type=int, required=True, help="Voxel grid X cells")
    parser.add_argument("--dy", type=int, required=True, help="Voxel grid Y cells")
    parser.add_argument("--dz", type=int, required=True, help="Voxel grid Z slices")
    parser.add_argument("--tile-size", type=int, required=True, help="Tile size used by spritesheet")
    parser.add_argument("--alpha-threshold", type=float, default=0.01, help="Minimum alpha to create a voxel")
    args = parser.parse_args(_script_args(sys.argv))

    png_path = os.path.abspath(args.png)
    if not os.path.isfile(png_path):
        raise FileNotFoundError(png_path)

    _clear_scene()
    image, width, height, pixels = _load_pixels(png_path)
    cells = _cells_from_stacked_png(pixels, width, height, args.dx, args.dy, args.dz, args.tile_size, args.alpha_threshold)
    verts, faces, face_cells = _build_voxel_mesh_data(cells, args.dx, args.dy, args.dz)

    mesh = bpy.data.meshes.new("Voxelator Preview Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Voxelator Preview", mesh)
    bpy.context.collection.objects.link(obj)
    _apply_colors(obj, face_cells, cells)
    _setup_scene(obj, image.name)

    print(f"Voxelator preview loaded: {png_path} voxels={len(cells)} faces={len(faces)}", flush=True)


if __name__ == "__main__":
    main()
