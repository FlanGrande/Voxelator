bl_info = {
    "name": "Voxelator",
    "author": "15shekels aka derpy.radio aka TITANDERP aka Ivan + forked by Flan",
    "version": (1, 2, 1),
    "blender": (4, 5, 1),
    "location": "View3D > Object",
    "description": "Converts any mesh into a voxelized mesh made up by cubes",
    "warning": "",
    "wiki_url": "",
    "category": "Object",
}


import bpy
import ctypes
import os
import subprocess
import sys
import time
import math

try:
    import numpy as np
except Exception:
    np = None
from mathutils import Vector, Matrix, noise
from mathutils.bvhtree import BVHTree
from bpy.props import (
    IntProperty,
    BoolProperty,
    StringProperty,
    EnumProperty
)
from bpy.types import (
    AddonPreferences,
    Operator,
    Panel,
    PropertyGroup
)

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voxelator.log")
LOG_TO_STDOUT = False
_BAKE_CACHE = {}

def _log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass
    if LOG_TO_STDOUT:
        try:
            print(str(msg), flush=True)
        except Exception:
            pass

class _ProgressReporter:
    """Small wrapper around Blender's status-bar progress API; no-op in headless contexts."""
    def __init__(self, context):
        self.wm = getattr(context, "window_manager", None)
        self.workspace = getattr(context, "workspace", None)
        self.started = False
        self.value = 0.0

    def begin(self, message="Starting"):
        if self.wm:
            try:
                self.wm.progress_begin(0.0, 100.0)
                self.started = True
            except Exception:
                self.started = False
        self.update(0.0, message)

    def update(self, value, message=None):
        value = max(0.0, min(100.0, float(value)))
        if value < self.value:
            value = self.value
        self.value = value
        if self.wm and self.started:
            try:
                self.wm.progress_update(self.value)
            except Exception:
                pass
        if message and self.workspace:
            try:
                self.workspace.status_text_set(f"Voxelator: {message} ({self.value:.0f}%)")
            except Exception:
                pass

    def end(self, message="Finished"):
        self.update(100.0, message)
        if self.workspace:
            try:
                self.workspace.status_text_set(None)
            except Exception:
                pass
        if self.wm and self.started:
            try:
                self.wm.progress_end()
            except Exception:
                pass
        self.started = False

def _progress_range(progress, start, end, index, total, message):
    if not progress or total <= 0:
        return
    progress.update(start + (end - start) * (index / total), message)

def _preview_python_candidates():
    seen = set()
    env_path = os.environ.get("VOXELATOR_PREVIEW_PYTHON", "").strip()
    candidates = [
        env_path,
        sys.executable,
        "/home/Flan/_Proyectos/.pyenv/versions/3.11.11/bin/python",
        "python3.11",
        "python3",
        "python",
    ]
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        yield candidate

def _find_preview_python():
    global _PREVIEW_PYTHON, _PREVIEW_PYTHON_TRIED
    if _PREVIEW_PYTHON_TRIED:
        return _PREVIEW_PYTHON
    _PREVIEW_PYTHON_TRIED = True
    check_code = "import imgui_bundle, PIL, numpy, OpenGL"
    for candidate in _preview_python_candidates():
        try:
            result = subprocess.run(
                [candidate, "-c", check_code],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:
            continue
        if result.returncode == 0:
            _PREVIEW_PYTHON = candidate
            return _PREVIEW_PYTHON
    _log("[Voxelator] Preview launch skipped: no Python with imgui_bundle, PIL, numpy, and PyOpenGL found. Set VOXELATOR_PREVIEW_PYTHON to override.")
    return None

def _launch_preview_process(png_path):
    if bpy.app.background:
        return
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview_voxel_slices.py")
    if not os.path.isfile(script_path):
        _log(f"[Voxelator] Preview script not found: {script_path}")
        return
    preview_python = _find_preview_python()
    if not preview_python:
        return

    cmd = [
        preview_python,
        script_path,
        "--png",
        png_path,
    ]
    log_path = os.path.splitext(png_path)[0] + ".preview.log"
    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True, cwd=os.path.dirname(script_path))
        _log(f"[Voxelator] Preview launched: {png_path} ({preview_python})")
    except Exception as exc:
        _log(f"[Voxelator] Preview launch failed: {exc}")

def _clamp01(value):
    return max(0.0, min(1.0, float(value)))

def _rgba_tuple(color, default=(1.0, 1.0, 1.0, 1.0)):
    if color is None:
        return default
    if isinstance(color, (int, float)):
        v = _clamp01(color)
        return (v, v, v, 1.0)
    try:
        r = _clamp01(color[0])
        g = _clamp01(color[1])
        b = _clamp01(color[2])
        a = _clamp01(color[3]) if len(color) > 3 else 1.0
        return (r, g, b, a)
    except Exception:
        return default

def _valid_image_node(node):
    return (
        node
        and node.type == 'TEX_IMAGE'
        and node.image
        and node.image.size[0] > 0
        and node.image.size[1] > 0
    )

def _linked_node(socket):
    if socket and getattr(socket, "is_linked", False) and socket.links:
        return socket.links[0].from_node
    return None

def _socket_default(socket):
    try:
        value = socket.default_value
    except Exception:
        return 0.0
    try:
        if len(value) >= 4:
            return _rgba_tuple(value)
        if len(value) == 3:
            return Vector((float(value[0]), float(value[1]), float(value[2])))
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return 0.0

def _as_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        if len(value) >= 3:
            return (float(value[0]) + float(value[1]) + float(value[2])) / 3.0
        if len(value) > 0:
            return float(value[0])
    except Exception:
        pass
    return 0.0

def _as_vector(value):
    try:
        if len(value) >= 3:
            return Vector((float(value[0]), float(value[1]), float(value[2])))
        if len(value) == 2:
            return Vector((float(value[0]), float(value[1]), 0.0))
    except Exception:
        pass
    scalar = _as_float(value)
    return Vector((scalar, scalar, scalar))

def _mix_rgba(a, b, factor):
    f = _clamp01(factor)
    ca = _rgba_tuple(a)
    cb = _rgba_tuple(b)
    return tuple(ca[i] * (1.0 - f) + cb[i] * f for i in range(4))

def _color_ramp_sample(ramp, factor):
    f = _clamp01(factor)
    elements = sorted(ramp.elements, key=lambda e: e.position)
    if not elements:
        return (1.0, 1.0, 1.0, 1.0)
    if f <= elements[0].position:
        return _rgba_tuple(elements[0].color)
    if f >= elements[-1].position:
        return _rgba_tuple(elements[-1].color)

    for i in range(len(elements) - 1):
        left = elements[i]
        right = elements[i + 1]
        if left.position <= f <= right.position:
            span = max(1e-8, right.position - left.position)
            t = (f - left.position) / span
            if ramp.interpolation == 'CONSTANT':
                t = 0.0
            elif ramp.interpolation == 'EASE':
                t = t * t * (3.0 - 2.0 * t)
            return _mix_rgba(left.color, right.color, t)
    return _rgba_tuple(elements[-1].color)

def _noise_factor(coord, scale, detail, roughness, lacunarity, distortion):
    p = Vector(coord) * max(0.0001, float(scale))
    if distortion:
        d = float(distortion)
        p += Vector((
            noise.noise(p + Vector((12.9898, 78.233, 37.719))),
            noise.noise(p + Vector((39.346, 11.135, 83.155))),
            noise.noise(p + Vector((73.156, 52.235, 9.151))),
        )) * d

    detail = max(0.0, float(detail))
    roughness = max(0.0, float(roughness))
    lacunarity = max(0.0001, float(lacunarity))
    octaves = max(1, int(math.floor(detail)))
    fractional = detail - math.floor(detail)
    amp = 1.0
    freq = 1.0
    total = 0.0
    norm = 0.0

    for _ in range(octaves):
        total += amp * ((noise.noise(p * freq) + 1.0) * 0.5)
        norm += amp
        amp *= roughness
        freq *= lacunarity
    if fractional > 1e-6:
        total += amp * fractional * ((noise.noise(p * freq) + 1.0) * 0.5)
        norm += amp * fractional
    return _clamp01(total / max(norm, 1e-8))

def _eval_node_socket(socket, sample_ctx, image_cache, seen=None, depth=0):
    if depth > 12:
        return _socket_default(socket)
    node = _linked_node(socket)
    if not node:
        return _socket_default(socket)
    seen = seen or set()
    key = (node.name, socket.links[0].from_socket.name if socket.links else "")
    if key in seen:
        return _socket_default(socket)
    seen.add(key)
    out_name = socket.links[0].from_socket.name if socket.links else ""

    if node.type == 'TEX_IMAGE':
        uv = sample_ctx.get("uv")
        sampled = _sample_image_bilinear(node.image, uv, image_cache) if _valid_image_node(node) and uv is not None else None
        if out_name == 'Alpha':
            return sampled[3] if sampled else 1.0
        return sampled if sampled else (1.0, 1.0, 1.0, 1.0)

    if node.type == 'VALTORGB':
        fac = _as_float(_eval_node_socket(node.inputs['Fac'], sample_ctx, image_cache, seen, depth + 1))
        color = _color_ramp_sample(node.color_ramp, fac)
        return color[3] if out_name == 'Alpha' else color

    if node.type == 'TEX_NOISE':
        vector = sample_ctx.get("generated", sample_ctx.get("local", Vector((0.0, 0.0, 0.0))))
        if 'Vector' in node.inputs and node.inputs['Vector'].is_linked:
            vector = _as_vector(_eval_node_socket(node.inputs['Vector'], sample_ctx, image_cache, seen, depth + 1))
        scale = _as_float(_eval_node_socket(node.inputs['Scale'], sample_ctx, image_cache, seen, depth + 1)) if 'Scale' in node.inputs else 5.0
        detail = _as_float(_eval_node_socket(node.inputs['Detail'], sample_ctx, image_cache, seen, depth + 1)) if 'Detail' in node.inputs else 2.0
        roughness = _as_float(_eval_node_socket(node.inputs['Roughness'], sample_ctx, image_cache, seen, depth + 1)) if 'Roughness' in node.inputs else 0.5
        lacunarity = _as_float(_eval_node_socket(node.inputs['Lacunarity'], sample_ctx, image_cache, seen, depth + 1)) if 'Lacunarity' in node.inputs else 2.0
        distortion = _as_float(_eval_node_socket(node.inputs['Distortion'], sample_ctx, image_cache, seen, depth + 1)) if 'Distortion' in node.inputs else 0.0
        fac = _noise_factor(vector, scale, detail, roughness, lacunarity, distortion)
        if out_name == 'Color':
            return (fac, fac, fac, 1.0)
        return fac

    if node.type == 'TEX_COORD':
        if out_name == 'UV' and sample_ctx.get("uv") is not None:
            u, v = sample_ctx["uv"]
            return Vector((u, v, 0.0))
        if out_name == 'Object':
            return sample_ctx.get("local", Vector((0.0, 0.0, 0.0)))
        return sample_ctx.get("generated", sample_ctx.get("local", Vector((0.0, 0.0, 0.0))))

    if node.type == 'MIX':
        factor = _as_float(_eval_node_socket(node.inputs['Factor'], sample_ctx, image_cache, seen, depth + 1)) if 'Factor' in node.inputs else 0.5
        a_name = 'A' if 'A' in node.inputs else 'Color1'
        b_name = 'B' if 'B' in node.inputs else 'Color2'
        if a_name in node.inputs and b_name in node.inputs:
            a = _eval_node_socket(node.inputs[a_name], sample_ctx, image_cache, seen, depth + 1)
            b = _eval_node_socket(node.inputs[b_name], sample_ctx, image_cache, seen, depth + 1)
            return _mix_rgba(a, b, factor)

    if node.type == 'MIX_RGB':
        factor = _as_float(_eval_node_socket(node.inputs['Fac'], sample_ctx, image_cache, seen, depth + 1))
        a = _eval_node_socket(node.inputs['Color1'], sample_ctx, image_cache, seen, depth + 1)
        b = _eval_node_socket(node.inputs['Color2'], sample_ctx, image_cache, seen, depth + 1)
        return _mix_rgba(a, b, factor)

    if node.type == 'RGB':
        return _rgba_tuple(node.outputs['Color'].default_value[:]) if 'Color' in node.outputs else (1.0, 1.0, 1.0, 1.0)
    if node.type == 'VALUE':
        return float(node.outputs['Value'].default_value) if 'Value' in node.outputs else 0.0

    if out_name in node.outputs and hasattr(node.outputs[out_name], "default_value"):
        return _socket_default(node.outputs[out_name])
    for input_socket in getattr(node, "inputs", []):
        if input_socket.is_linked:
            return _eval_node_socket(input_socket, sample_ctx, image_cache, seen, depth + 1)
    return _socket_default(socket)

def _get_color_from_material(mat):
    if not mat:
        return (1.0, 1.0, 1.0, 1.0)
    if not getattr(mat, "use_nodes", False):
        if hasattr(mat, "diffuse_color"):
            c = mat.diffuse_color
            return _rgba_tuple(c)
        return (1.0, 1.0, 1.0, 1.0)

    for node in mat.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            return _rgba_tuple(node.inputs['Base Color'].default_value[:])
        if node.type == 'BSDF_TOON':
            return _rgba_tuple(node.inputs['Color'].default_value[:])
    return (1.0, 1.0, 1.0, 1.0)

def _get_material_color_source(mat, mat_source_cache):
    cached = mat_source_cache.get(mat.name)
    if cached is not None:
        return cached

    fallback = _get_color_from_material(mat)
    source = ("solid", fallback)

    if mat and getattr(mat, "use_nodes", False) and mat.node_tree:
        socket = None
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                socket = node.inputs['Base Color']
                fallback = _rgba_tuple(socket.default_value[:])
                break
            if node.type == 'BSDF_TOON':
                socket = node.inputs['Color']
                fallback = _rgba_tuple(socket.default_value[:])
                break

        if socket and socket.is_linked:
            linked_node = _linked_node(socket)
            if _valid_image_node(linked_node):
                source = ("image", linked_node.image, fallback)
            else:
                source = ("node", socket, fallback)

    mat_source_cache[mat.name] = source
    return source

def _image_pixels_flat(image):
    """Fast flat pixel buffer for an image, or None if empty."""
    w = int(image.size[0])
    h = int(image.size[1])
    if w <= 0 or h <= 0:
        return None
    count = w * h * 4
    if np is not None:
        pixels = np.empty(count, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    else:
        pixels = [0.0] * count
        image.pixels.foreach_get(pixels)
        pixels = tuple(pixels)
    return (w, h, pixels)

def _sample_image_bilinear(image, uv, image_cache):
    img_key = image.name
    cached = image_cache.get(img_key)
    if cached is None:
        cached = _image_pixels_flat(image)
        if cached is None:
            return None
        image_cache[img_key] = cached

    w, h, pixels = cached
    return _sample_pixels_bilinear(w, h, pixels, uv)

def _sample_pixels_bilinear(w, h, pixels, uv):
    u = uv[0] % 1.0
    v = uv[1] % 1.0

    x = u * (w - 1)
    y = v * (h - 1)
    x0 = int(math.floor(x))
    y0 = int(math.floor(y))
    x1 = min(x0 + 1, w - 1)
    y1 = min(y0 + 1, h - 1)
    tx = x - x0
    ty = y - y0

    def px(ix, iy):
        idx = (iy * w + ix) * 4
        return (float(pixels[idx]), float(pixels[idx + 1]), float(pixels[idx + 2]), float(pixels[idx + 3]))

    c00 = px(x0, y0)
    c10 = px(x1, y0)
    c01 = px(x0, y1)
    c11 = px(x1, y1)

    out = [0.0, 0.0, 0.0, 0.0]
    for i in range(4):
        a = c00[i] * (1.0 - tx) + c10[i] * tx
        b = c01[i] * (1.0 - tx) + c11[i] * tx
        out[i] = a * (1.0 - ty) + b * ty
    return tuple(out)

def _barycentric_coords(point, a, b, c):
    v0 = b - a
    v1 = c - a
    v2 = point - a
    d00 = v0.dot(v0)
    d01 = v0.dot(v1)
    d11 = v1.dot(v1)
    d20 = v2.dot(v0)
    d21 = v2.dot(v1)
    denom = d00 * d11 - d01 * d01
    if abs(denom) < 1e-12:
        return None
    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    u = 1.0 - v - w
    return (u, v, w)

def _uv_from_triangle(location_local, loop_tri, uv_data, verts):
    loops_idx = loop_tri.loops
    verts_idx = loop_tri.vertices
    a = verts[verts_idx[0]].co
    b = verts[verts_idx[1]].co
    c = verts[verts_idx[2]].co
    bary = _barycentric_coords(location_local, a, b, c)
    if bary is None:
        return None

    u, v, w = bary
    uv0 = uv_data[loops_idx[0]].uv
    uv1 = uv_data[loops_idx[1]].uv
    uv2 = uv_data[loops_idx[2]].uv
    uv_x = uv0.x * u + uv1.x * v + uv2.x * w
    uv_y = uv0.y * u + uv1.y * v + uv2.y * w
    penalty = max(0.0, -u) + max(0.0, -v) + max(0.0, -w)
    return (float(uv_x), float(uv_y), penalty)

def _estimate_face_uv(location_local, poly, uv_data, loops, verts, loop_tris_by_poly=None):
    if loop_tris_by_poly:
        best = None
        for loop_tri in loop_tris_by_poly.get(poly.index, ()):
            uv = _uv_from_triangle(location_local, loop_tri, uv_data, verts)
            if uv is None:
                continue
            if uv[2] <= 1e-5:
                return (uv[0], uv[1])
            if best is None or uv[2] < best[2]:
                best = uv
        if best is not None:
            return (best[0], best[1])

    sum_u = 0.0
    sum_v = 0.0
    sum_w = 0.0
    eps = 1e-8
    for li in poly.loop_indices:
        vi = loops[li].vertex_index
        vco = verts[vi].co
        d = (location_local - vco).length
        w = 1.0 / max(d, eps)
        luv = uv_data[li].uv
        sum_u += luv.x * w
        sum_v += luv.y * w
        sum_w += w

    if sum_w <= eps:
        first_uv = uv_data[poly.loop_indices[0]].uv
        return (float(first_uv.x), float(first_uv.y))
    return (sum_u / sum_w, sum_v / sum_w)

def _build_layer_color_map(dx, dy, dz, cube_color_map):
    layers = [{} for _ in range(dz)]
    for (ix, iy, iz), color in cube_color_map.items():
        if 0 <= ix < dx and 0 <= iy < dy and 0 <= iz < dz and color:
            layers[iz][(ix, iy)] = color
    return layers

def _render_layers_into_pixels(px, width, height, layers, dx, dy, dz, tile_size=None, row_count=1, row_index=0, align_left=False, progress=None, progress_start=0.0, progress_end=100.0, progress_label="Building spritesheet"):
    tile = int(tile_size) if tile_size is not None else max(dx, dy)
    off_x = 0 if align_left else (tile - dx) // 2
    off_y = (tile - dy) // 2
    row_bottom = row_index * tile
    step_z = max(1, dz // 10)

    for z in range(dz):
        x0 = z * tile
        for (ix, iy), color in layers[z].items():
            px_x = x0 + off_x + ix
            px_y = row_bottom + off_y + iy
            if 0 <= px_x < width and 0 <= px_y < height:
                idx = ((height - 1 - px_y) * width + px_x) * 4
                px[idx] = color[0]
                px[idx + 1] = color[1]
                px[idx + 2] = color[2]
                px[idx + 3] = color[3] if len(color) > 3 else 1.0
        if row_count == 1 and (((z + 1) % step_z) == 0 or (z + 1) == dz):
            _log(f"[Voxelator] Spritesheet fill {z+1}/{dz}")
            _progress_range(progress, progress_start, progress_end, z + 1, dz, progress_label)

def _save_voxel_spritesheet(dx, dy, dz, filepath, cube_color_map, tile_size, progress=None, progress_start=85.0, progress_end=95.0):
    layers = _build_layer_color_map(dx, dy, dz, cube_color_map)

    cube_count = len(cube_color_map)
    _log(f"[Voxelator] Building spritesheet from {cube_count} cubes; grid: {dx} {dy} {dz}")

    tile = max(1, int(tile_size))
    if dx > tile or dy > tile:
        _log(f"[Voxelator] Warning: grid {dx}x{dy} exceeds tile {tile} and may clip")
    width = tile * dz
    height = tile
    abs_path = bpy.path.abspath(filepath)
    base = os.path.splitext(os.path.basename(abs_path))[0]
    img = bpy.data.images.new(f"voxel_slices_{base}", width=width, height=height, alpha=True, float_buffer=False)
    px = [0.0] * (width * height * 4)
    _log(f"[Voxelator] Spritesheet dimensions: {width} x {height}")
    _render_layers_into_pixels(px, width, height, layers, dx, dy, dz, tile_size=tile, progress=progress, progress_start=progress_start, progress_end=progress_end, progress_label="Building spritesheet")
    if progress:
        progress.update(progress_end, "Saving spritesheet PNG")
    img.pixels.foreach_set(px)
    img.filepath_raw = abs_path
    img.file_format = 'PNG'
    img.save()
    _log(f"[Voxelator] Saved spritesheet: {abs_path}")

def _save_voxel_animation_spritesheet(frame_color_maps, dx, dy, dz, filepath, tile_size, progress=None, progress_start=85.0, progress_end=100.0):
    frame_count = len(frame_color_maps)
    tile = max(1, int(tile_size))
    if dx > tile or dy > tile:
        _log(f"[Voxelator] Warning: grid {dx}x{dy} exceeds tile {tile} and may clip")
    width = tile * dz
    height = tile * frame_count
    abs_path = bpy.path.abspath(filepath)
    base = os.path.splitext(os.path.basename(abs_path))[0]
    img = bpy.data.images.new(f"voxel_anim_slices_{base}", width=width, height=height, alpha=True, float_buffer=False)
    px = [0.0] * (width * height * 4)

    _log(f"[Voxelator] Building animation spritesheet frames={frame_count} grid={dx} {dy} {dz}")
    _log(f"[Voxelator] Animation spritesheet dimensions: {width} x {height}")

    for i, cube_color_map in enumerate(frame_color_maps):
        layers = _build_layer_color_map(dx, dy, dz, cube_color_map)
        _render_layers_into_pixels(px, width, height, layers, dx, dy, dz, tile_size=tile, row_count=frame_count, row_index=i, align_left=False)
        _log(f"[Voxelator] Animation row {i+1}/{frame_count}")
        _progress_range(progress, progress_start, progress_end, i + 1, frame_count, "Building animation spritesheet")

    if progress:
        progress.update(progress_end, "Saving animation spritesheet PNG")
    img.pixels.foreach_set(px)
    img.filepath_raw = abs_path
    img.file_format = 'PNG'
    img.save()
    _log(f"[Voxelator] Saved animation spritesheet: {abs_path}")

def _plane_box_overlap(normal, vert, maxbox):
    nx, ny, nz = normal
    vx, vy, vz = vert
    mx, my, mz = maxbox

    if nx > 0.0:
        vmin_x = -mx - vx
        vmax_x = mx - vx
    else:
        vmin_x = mx - vx
        vmax_x = -mx - vx

    if ny > 0.0:
        vmin_y = -my - vy
        vmax_y = my - vy
    else:
        vmin_y = my - vy
        vmax_y = -my - vy

    if nz > 0.0:
        vmin_z = -mz - vz
        vmax_z = mz - vz
    else:
        vmin_z = mz - vz
        vmax_z = -mz - vz

    if (nx * vmin_x + ny * vmin_y + nz * vmin_z) > 0.0:
        return False
    if (nx * vmax_x + ny * vmax_y + nz * vmax_z) >= 0.0:
        return True
    return False

def _tri_box_overlap(center, half_size, tri):
    cx, cy, cz = center
    hx, hy, hz = half_size
    (ax, ay, az), (bx, by, bz), (cx2, cy2, cz2) = tri

    v0x = ax - cx
    v0y = ay - cy
    v0z = az - cz
    v1x = bx - cx
    v1y = by - cy
    v1z = bz - cz
    v2x = cx2 - cx
    v2y = cy2 - cy
    v2z = cz2 - cz

    e0x = v1x - v0x
    e0y = v1y - v0y
    e0z = v1z - v0z
    e1x = v2x - v1x
    e1y = v2y - v1y
    e1z = v2z - v1z
    e2x = v0x - v2x
    e2y = v0y - v2y
    e2z = v0z - v2z

    def axis_test(axv, ayv, azv):
        p0 = axv * v0x + ayv * v0y + azv * v0z
        p1 = axv * v1x + ayv * v1y + azv * v1z
        p2 = axv * v2x + ayv * v2y + azv * v2z
        min_p = min(p0, p1, p2)
        max_p = max(p0, p1, p2)
        rad = hx * abs(axv) + hy * abs(ayv) + hz * abs(azv)
        return not (min_p > rad or max_p < -rad)

    axes = (
        (0.0, -e0z, e0y), (e0z, 0.0, -e0x), (-e0y, e0x, 0.0),
        (0.0, -e1z, e1y), (e1z, 0.0, -e1x), (-e1y, e1x, 0.0),
        (0.0, -e2z, e2y), (e2z, 0.0, -e2x), (-e2y, e2x, 0.0),
    )
    for axv, ayv, azv in axes:
        if not axis_test(axv, ayv, azv):
            return False

    min_x = min(v0x, v1x, v2x)
    max_x = max(v0x, v1x, v2x)
    if min_x > hx or max_x < -hx:
        return False

    min_y = min(v0y, v1y, v2y)
    max_y = max(v0y, v1y, v2y)
    if min_y > hy or max_y < -hy:
        return False

    min_z = min(v0z, v1z, v2z)
    max_z = max(v0z, v1z, v2z)
    if min_z > hz or max_z < -hz:
        return False

    nx = e0y * e1z - e0z * e1y
    ny = e0z * e1x - e0x * e1z
    nz = e0x * e1y - e0y * e1x
    if not _plane_box_overlap((nx, ny, nz), (v0x, v0y, v0z), (hx, hy, hz)):
        return False

    return True

def _world_verts_np(mesh, matrix_world):
    """All mesh vertices transformed by matrix_world as an (N, 3) float64 array."""
    count = len(mesh.vertices)
    buf = np.empty(count * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", buf)
    co = buf.reshape(count, 3)
    m = np.array(matrix_world, dtype=np.float64)
    return co @ m[:3, :3].T + m[:3, 3]

def _world_bounds(mesh, matrix_world):
    """(min_xyz, max_xyz) tuples of mesh vertices under matrix_world, or None if empty."""
    if not len(mesh.vertices):
        return None
    if np is not None:
        pts = _world_verts_np(mesh, matrix_world)
        mn = pts.min(axis=0)
        mx = pts.max(axis=0)
        return (float(mn[0]), float(mn[1]), float(mn[2])), (float(mx[0]), float(mx[1]), float(mx[2]))
    verts_world = [matrix_world @ v.co for v in mesh.vertices]
    return (
        (min(v.x for v in verts_world), min(v.y for v in verts_world), min(v.z for v in verts_world)),
        (max(v.x for v in verts_world), max(v.y for v in verts_world), max(v.z for v in verts_world)),
    )

_NATIVE_FN = None
_NATIVE_TRIED = False
_NATIVE_LIB = None
_NATIVE_MAP_FN = None
_NATIVE_MAP_TRIED = False
_PREVIEW_PYTHON = None
_PREVIEW_PYTHON_TRIED = False

def _get_native_voxelizer():
    """Compile (first run) and load libvoxelize.so. Returns the ctypes function or None."""
    global _NATIVE_FN, _NATIVE_TRIED, _NATIVE_LIB
    if _NATIVE_TRIED:
        return _NATIVE_FN
    _NATIVE_TRIED = True

    base_dir = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(base_dir, "voxelize_native.c")
    lib_path = os.path.join(base_dir, "libvoxelize.so")
    if not os.path.isfile(src_path):
        _log(f"[Voxelator] Native voxelizer source not found: {src_path}")
        return None

    try:
        needs_build = (not os.path.isfile(lib_path)) or os.path.getmtime(lib_path) < os.path.getmtime(src_path)
        if needs_build:
            proc = None
            for flags in (("-O3", "-fopenmp"), ("-O3",)):
                cmd = ["cc", *flags, "-shared", "-fPIC", "-o", lib_path, src_path]
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode == 0:
                    _log(f"[Voxelator] Compiled native voxelizer: {' '.join(cmd)}")
                    break
            else:
                err = (proc.stderr or "").strip()[:300] if proc else "cc not available"
                _log(f"[Voxelator] Native voxelizer compile failed: {err}")
                return None

        lib = ctypes.CDLL(lib_path)
        fn = lib.voxelize_surface
        fn.restype = ctypes.c_int
        fn.argtypes = (
            ctypes.POINTER(ctypes.c_double), ctypes.c_int64,
            ctypes.POINTER(ctypes.c_int64), ctypes.c_int64,
            ctypes.c_double,
            ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_int64, ctypes.c_int64, ctypes.c_int64,
            ctypes.POINTER(ctypes.c_uint8),
        )
        _NATIVE_LIB = lib
        _NATIVE_FN = fn
        _log("[Voxelator] Native voxelizer loaded")
    except Exception as exc:
        _log(f"[Voxelator] Native voxelizer unavailable: {exc}")
        _NATIVE_FN = None
    return _NATIVE_FN

def _get_native_color_mapper():
    """Bind map_colors from libvoxelize.so. Returns the ctypes function or None."""
    global _NATIVE_MAP_FN, _NATIVE_MAP_TRIED
    if _NATIVE_MAP_TRIED:
        return _NATIVE_MAP_FN
    _NATIVE_MAP_TRIED = True
    if _get_native_voxelizer() is None or _NATIVE_LIB is None:
        return None
    try:
        fn = _NATIVE_LIB.map_colors
        fn.restype = ctypes.c_int
        fn.argtypes = (
            ctypes.POINTER(ctypes.c_double), ctypes.c_int64,
            ctypes.POINTER(ctypes.c_int64), ctypes.c_int64,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float), ctypes.c_int64, ctypes.c_int64,
            ctypes.POINTER(ctypes.c_double), ctypes.c_int64,
            ctypes.POINTER(ctypes.c_float),
        )
        _NATIVE_MAP_FN = fn
        _log("[Voxelator] Native color mapper loaded")
    except Exception as exc:
        _log(f"[Voxelator] Native color mapper unavailable: {exc}")
        _NATIVE_MAP_FN = None
    return _NATIVE_MAP_FN

def _build_occupied_cells_native(mesh, matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz):
    """Native surface voxelize. Returns set of (ix, iy, iz) or None if unavailable."""
    if np is None:
        return None
    fn = _get_native_voxelizer()
    if fn is None:
        return None

    verts_w = np.ascontiguousarray(_world_verts_np(mesh, matrix_world), dtype=np.float64)
    tri_count = len(mesh.loop_triangles)
    tris = np.empty(tri_count * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get("vertices", tris)
    occ = np.zeros(dx * dy * dz, dtype=np.uint8)

    rc = fn(
        verts_w.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(mesh.vertices),
        tris.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)), tri_count,
        float(cell_len),
        float(grid_min_x), float(grid_min_y), float(grid_min_z),
        int(dx), int(dy), int(dz),
        occ.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
    )
    if rc != 0:
        _log(f"[Voxelator] Native voxelize returned error code {rc}")
        return None

    xs, ys, zs = np.nonzero(occ.reshape(dx, dy, dz))
    _log(f"[Voxelator] Surface voxelize (native): {tri_count} triangles")
    return {(int(x), int(y), int(z)) for x, y, z in zip(xs, ys, zs)}

def _build_occupied_cells_from_mesh(mesh, matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz):
    mesh.calc_loop_triangles()
    try:
        cells = _build_occupied_cells_native(mesh, matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz)
        if cells is not None:
            return cells
    except Exception as exc:
        _log(f"[Voxelator] Native voxelize failed, falling back to Python: {exc}")
    return _build_occupied_cells_py(mesh, matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz)

def _build_occupied_cells_py(mesh, matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz):
    verts_w = [matrix_world @ v.co for v in mesh.vertices]

    half = 0.5 * cell_len
    shell = set()
    tris = mesh.loop_triangles
    total_tris = len(tris)
    step = max(1, total_tris // 10) if total_tris else 1

    for ti, tri in enumerate(tris):
        a = verts_w[tri.vertices[0]]
        b = verts_w[tri.vertices[1]]
        c = verts_w[tri.vertices[2]]
        tri_pts = ((a.x, a.y, a.z), (b.x, b.y, b.z), (c.x, c.y, c.z))

        min_x = min(a.x, b.x, c.x)
        min_y = min(a.y, b.y, c.y)
        min_z = min(a.z, b.z, c.z)
        max_x = max(a.x, b.x, c.x)
        max_y = max(a.y, b.y, c.y)
        max_z = max(a.z, b.z, c.z)

        ix0 = max(0, int(math.floor((min_x - grid_min_x) / cell_len)) - 1)
        iy0 = max(0, int(math.floor((min_y - grid_min_y) / cell_len)) - 1)
        iz0 = max(0, int(math.floor((min_z - grid_min_z) / cell_len)) - 1)
        ix1 = min(dx - 1, int(math.floor((max_x - grid_min_x) / cell_len)) + 1)
        iy1 = min(dy - 1, int(math.floor((max_y - grid_min_y) / cell_len)) + 1)
        iz1 = min(dz - 1, int(math.floor((max_z - grid_min_z) / cell_len)) + 1)

        if ix1 < ix0 or iy1 < iy0 or iz1 < iz0:
            continue

        for ix in range(ix0, ix1 + 1):
            cx = grid_min_x + (ix + 0.5) * cell_len
            for iy in range(iy0, iy1 + 1):
                cy = grid_min_y + (iy + 0.5) * cell_len
                for iz in range(iz0, iz1 + 1):
                    cz = grid_min_z + (iz + 0.5) * cell_len
                    if _tri_box_overlap((cx, cy, cz), (half, half, half), tri_pts):
                        shell.add((ix, iy, iz))

        if ((ti + 1) % step) == 0 or (ti + 1) == total_tris:
            _log(f"[Voxelator] Surface voxelize {ti+1}/{total_tris}")

    return shell

def _build_voxel_mesh_data(occupied_cells, ox, oy, oz, cell_len, separate_cubes):
    face_defs = (
        ((1, 0, 0), ((1, -1, -1), (1, -1, 1), (1, 1, 1), (1, 1, -1))),
        ((-1, 0, 0), ((-1, -1, -1), (-1, 1, -1), (-1, 1, 1), (-1, -1, 1))),
        ((0, 1, 0), ((-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1))),
        ((0, -1, 0), ((-1, -1, -1), (-1, -1, 1), (1, -1, 1), (1, -1, -1))),
        ((0, 0, 1), ((-1, -1, 1), (-1, 1, 1), (1, 1, 1), (1, -1, 1))),
        ((0, 0, -1), ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1))),
    )

    verts = []
    faces = []
    face_cells = []
    vert_map = {}
    half = 0.5 * cell_len

    for cell in sorted(occupied_cells):
        ix, iy, iz = cell
        for normal, corners in face_defs:
            nx, ny, nz = normal
            if (not separate_cubes) and ((ix + nx, iy + ny, iz + nz) in occupied_cells):
                continue

            face = []
            for sx, sy, sz in corners:
                lx = 2 * ix + sx
                ly = 2 * iy + sy
                lz = 2 * iz + sz
                key = (lx, ly, lz)

                if separate_cubes:
                    vx = ox + lx * half
                    vy = oy + ly * half
                    vz = oz + lz * half
                    verts.append((vx, vy, vz))
                    face.append(len(verts) - 1)
                else:
                    vi = vert_map.get(key)
                    if vi is None:
                        vx = ox + lx * half
                        vy = oy + ly * half
                        vz = oz + lz * half
                        vi = len(verts)
                        verts.append((vx, vy, vz))
                        vert_map[key] = vi
                    face.append(vi)

            faces.append(face)
            face_cells.append(cell)

    return verts, faces, face_cells

def _make_voxel_color_material():
    mat = bpy.data.materials.new("Voxelator_VoxelColor")
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    mat.use_nodes = True

    nodes = mat.node_tree.nodes
    bsdf = next((node for node in nodes if node.type == 'BSDF_PRINCIPLED'), None)
    if bsdf:
        attr = nodes.new(type='ShaderNodeAttribute')
        attr.attribute_name = "VoxelColor"
        if 'Color' in attr.outputs and 'Base Color' in bsdf.inputs:
            mat.node_tree.links.new(attr.outputs['Color'], bsdf.inputs['Base Color'])
        if 'Alpha' in attr.outputs and 'Alpha' in bsdf.inputs:
            mat.node_tree.links.new(attr.outputs['Alpha'], bsdf.inputs['Alpha'])
            mat.blend_method = 'BLEND'
    return mat

def _apply_voxel_color_attribute(obj, face_cells, cube_color_map):
    mesh = obj.data
    color_attr = mesh.color_attributes.new(name="VoxelColor", type='BYTE_COLOR', domain='CORNER')
    fallback = (1.0, 1.0, 1.0, 1.0)

    polys = mesh.polygons
    total_p = len(polys)
    total_loops = len(mesh.loops)

    loop_starts = [0] * total_p
    loop_totals = [0] * total_p
    polys.foreach_get("loop_start", loop_starts)
    polys.foreach_get("loop_total", loop_totals)

    flat = [1.0] * (total_loops * 4)
    face_cell_count = len(face_cells)
    for pi in range(total_p):
        if pi < face_cell_count:
            color = _rgba_tuple(cube_color_map.get(face_cells[pi]), fallback)
        else:
            color = fallback
        start = loop_starts[pi]
        for li in range(start, start + loop_totals[pi]):
            base = li * 4
            flat[base] = color[0]
            flat[base + 1] = color[1]
            flat[base + 2] = color[2]
            flat[base + 3] = color[3]

    color_attr.data.foreach_set("color", flat)

    mesh.materials.clear()
    mesh.materials.append(_make_voxel_color_material())
    mesh.update()
    return total_p

def _animation_items_for_object(self, context):
    obj = context.object if context else None
    linked_actions = {}

    def add_linked_action(action):
        if action:
            linked_actions[action.name] = action

    def add_from_anim_data(id_data):
        if not id_data:
            return
        anim = getattr(id_data, "animation_data", None)
        if not anim:
            return

        add_linked_action(anim.action)
        for track in anim.nla_tracks:
            for strip in track.strips:
                add_linked_action(strip.action)

    if obj:
        add_from_anim_data(obj)
        add_from_anim_data(getattr(obj, "data", None))

        shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
        add_from_anim_data(shape_keys)

        linked_armatures = []
        if obj.parent and obj.parent.type == 'ARMATURE':
            linked_armatures.append(obj.parent)
        for mod in getattr(obj, "modifiers", []):
            if mod.type == 'ARMATURE' and mod.object:
                linked_armatures.append(mod.object)

        seen_armatures = set()
        for arm in linked_armatures:
            if arm.name in seen_armatures:
                continue
            seen_armatures.add(arm.name)
            add_from_anim_data(arm)
            add_from_anim_data(getattr(arm, "data", None))

    action_names = sorted(action.name for action in bpy.data.actions)
    if not action_names:
        return [('NONE', 'No animations found', 'No actions available in this project')]

    items = []
    for name in action_names:
        if name in linked_actions:
            label = f"{name} (Added manually)"
            desc = f"Action currently linked in NLA/animation data"
        else:
            label = f"{name} (Detected)"
            desc = f"Action available in this project"
        items.append((name, label, desc))
    return items

def _get_animation_owner(obj):
    if obj.parent and obj.parent.type == 'ARMATURE':
        return obj.parent
    for mod in getattr(obj, "modifiers", []):
        if mod.type == 'ARMATURE' and mod.object:
            return mod.object
    return obj

def _mesh_from_source(source, depsgraph, apply_modifiers):
    if apply_modifiers:
        source_eval = source.evaluated_get(depsgraph)
        return bpy.data.meshes.new_from_object(source_eval, preserve_all_data_layers=True, depsgraph=depsgraph)
    if source.type == 'MESH':
        return source.data.copy()
    return bpy.data.meshes.new_from_object(source, preserve_all_data_layers=True, depsgraph=depsgraph)

def _mesh_bounds(verts):
    if not verts:
        zero = Vector((0.0, 0.0, 0.0))
        return zero, zero
    min_v = Vector((min(v.co.x for v in verts), min(v.co.y for v in verts), min(v.co.z for v in verts)))
    max_v = Vector((max(v.co.x for v in verts), max(v.co.y for v in verts), max(v.co.z for v in verts)))
    return min_v, max_v

def _generated_coord(location, min_v, max_v):
    span = max_v - min_v
    return Vector((
        (location.x - min_v.x) / span.x if abs(span.x) > 1e-8 else 0.0,
        (location.y - min_v.y) / span.y if abs(span.y) > 1e-8 else 0.0,
        (location.z - min_v.z) / span.z if abs(span.z) > 1e-8 else 0.0,
    ))

def _uv_from_loop_tri_flat(location_local, loop_tri, uv_flat, verts):
    verts_idx = loop_tri.vertices
    a = verts[verts_idx[0]].co
    b = verts[verts_idx[1]].co
    c = verts[verts_idx[2]].co
    bary = _barycentric_coords(location_local, a, b, c)
    if bary is None:
        return None
    u, v, w = bary
    l0, l1, l2 = loop_tri.loops
    uv_x = uv_flat[l0 * 2] * u + uv_flat[l1 * 2] * v + uv_flat[l2 * 2] * w
    uv_y = uv_flat[l0 * 2 + 1] * u + uv_flat[l1 * 2 + 1] * v + uv_flat[l2 * 2 + 1] * w
    return (float(uv_x), float(uv_y))

def _effective_bake_resolution(bake_resolution, voxel_resolution):
    """Bake resolution scaled with voxel resolution; the user setting acts as a cap.

    Small voxel grids cannot use the extra texel density, so baking above
    ~8 texels per voxel cell edge only costs Cycles time.
    """
    return min(max(64, int(bake_resolution)), max(64, int(voxel_resolution) * 8))

def _bake_base_color_image(context, obj, resolution):
    """Bake the exact evaluated Base Color (Cycles diffuse color pass) of all
    materials on obj, using a dedicated non-overlapping UV layer.
    Returns ((w, h, pixels), uv_flat) or (None, None) on failure.
    The temporary bake image is always removed before returning."""
    mesh = obj.data
    if not any(mesh.materials):
        _log("[Voxelator] Bake skipped: no materials")
        return None, None

    scene = context.scene
    prev_engine = scene.render.engine
    prev_samples = None
    prev_active = context.view_layer.objects.active
    prev_selected = list(context.selected_objects)
    temp_nodes = []
    restore_use_nodes = []
    image = None

    try:
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        bake_uv = mesh.uv_layers.new(name="VoxelBake")
        if bake_uv is None:
            _log("[Voxelator] Bake skipped: could not create UV layer")
            return None, None
        mesh.uv_layers.active = bake_uv

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02)
        bpy.ops.object.mode_set(mode='OBJECT')

        resolution = max(64, int(resolution))
        image = bpy.data.images.new(f"VoxelBake_{obj.name}", width=resolution, height=resolution, alpha=True, float_buffer=False)

        for mat in mesh.materials:
            if not mat:
                continue
            if not mat.use_nodes:
                restore_use_nodes.append(mat)
                mat.use_nodes = True
            node_tree = mat.node_tree
            tex = node_tree.nodes.new('ShaderNodeTexImage')
            tex.image = image
            tex.select = True
            node_tree.nodes.active = tex
            temp_nodes.append((node_tree, tex))

        scene.render.engine = 'CYCLES'
        try:
            prev_samples = scene.cycles.samples
            scene.cycles.samples = 1
        except Exception:
            prev_samples = None

        bake_start = time.perf_counter()
        bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, margin=4, use_selected_to_active=False)
        _log(f"[Voxelator][Timing] Base color bake ({resolution}x{resolution}): {time.perf_counter() - bake_start:.3f}s")

        uv_flat = [0.0] * (len(mesh.loops) * 2)
        mesh.uv_layers["VoxelBake"].data.foreach_get("uv", uv_flat)
        pixels_flat = _image_pixels_flat(image)
        return pixels_flat, uv_flat
    except Exception as exc:
        _log(f"[Voxelator] Bake failed, falling back to node sampling: {exc}")
        return None, None
    finally:
        for node_tree, tex in temp_nodes:
            try:
                node_tree.nodes.remove(tex)
            except Exception:
                pass
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except Exception:
                pass
        for mat in restore_use_nodes:
            try:
                mat.use_nodes = False
            except Exception:
                pass
        if prev_samples is not None:
            try:
                scene.cycles.samples = prev_samples
            except Exception:
                pass
        try:
            scene.render.engine = prev_engine
        except Exception:
            pass
        for o in context.selected_objects:
            o.select_set(False)
        for o in prev_selected:
            try:
                o.select_set(True)
            except Exception:
                pass
        try:
            context.view_layer.objects.active = prev_active
        except Exception:
            pass

def _build_cube_maps_native(source_mesh, source_inv, occ_list, ox, oy, oz, cell_len, bake_w, bake_h, bake_px, bake_uv_flat):
    """Native (C) nearest-triangle bake sampling for all occupied cells.
    Returns (mapped_count, cube_color_map) or None when unavailable."""
    if np is None or not occ_list:
        return None
    fn = _get_native_color_mapper()
    if fn is None:
        return None
    n_tris = len(source_mesh.loop_triangles)
    n_verts = len(source_mesh.vertices)
    if n_tris <= 0 or n_verts <= 0:
        return None
    try:
        vert_buf = np.empty(n_verts * 3, dtype=np.float64)
        source_mesh.vertices.foreach_get("co", vert_buf)
        tri_buf = np.empty(n_tris * 3, dtype=np.int64)
        source_mesh.loop_triangles.foreach_get("vertices", tri_buf)
        loop_buf = np.empty(n_tris * 3, dtype=np.int64)
        source_mesh.loop_triangles.foreach_get("loops", loop_buf)

        uvs = np.asarray(bake_uv_flat, dtype=np.float32).reshape(-1, 2)
        tri_uvs = np.ascontiguousarray(uvs[loop_buf].reshape(-1), dtype=np.float32)
        px = np.ascontiguousarray(bake_px, dtype=np.float32)
        if px.size != bake_w * bake_h * 4:
            return None

        cells = np.asarray(occ_list, dtype=np.float64)
        centers = cells * float(cell_len) + np.array([ox, oy, oz], dtype=np.float64)
        m = np.array([[source_inv[i][j] for j in range(4)] for i in range(4)], dtype=np.float64)
        local = np.ascontiguousarray((centers @ m[:3, :3].T + m[:3, 3]).reshape(-1), dtype=np.float64)

        out = np.empty(len(occ_list) * 4, dtype=np.float32)
        rc = fn(
            vert_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), n_verts,
            tri_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)), n_tris,
            tri_uvs.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            px.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), int(bake_w), int(bake_h),
            local.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(occ_list),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        )
        if rc != 0:
            _log(f"[Voxelator] Native color map returned error code {rc}")
            return None
        colors = out.reshape(-1, 4)
        cube_color_map = {}
        for cell, col in zip(occ_list, colors):
            cube_color_map[cell] = (float(col[0]), float(col[1]), float(col[2]), float(col[3]))
        return len(occ_list), cube_color_map
    except Exception as exc:
        _log(f"[Voxelator] Native color map failed, falling back to Python: {exc}")
        return None

def _build_cube_maps(source, occupied, ox, oy, oz, cell_len, world_to_source_matrix=None, mat_source_cache=None, image_cache=None, bake_data=None, progress=None, progress_start=45.0, progress_end=85.0, progress_label="Mapping voxel colors"):
    mapped_count = 0
    cube_color_map = {}
    source_inv = world_to_source_matrix if world_to_source_matrix is not None else source.matrix_world.inverted()
    source_mesh = source.data
    source_mesh.calc_loop_triangles()
    source_polys = source_mesh.polygons
    source_mats = source_mesh.materials
    source_loops = source_mesh.loops
    source_verts = source_mesh.vertices
    bake_pixels = None
    bake_uv_flat = None
    if bake_data:
        bake_pixels, bake_uv_flat = bake_data
    use_bake = bool(
        bake_pixels is not None
        and bake_uv_flat
        and len(bake_uv_flat) == len(source_loops) * 2
    )
    bake_w = bake_h = 0
    bake_px = None
    if use_bake:
        bake_w, bake_h, bake_px = bake_pixels
    if bake_data and not use_bake:
        _log("[Voxelator] Bake data mismatch with mesh loops; using fallback sampling")

    if use_bake:
        native_start = time.perf_counter()
        native_result = _build_cube_maps_native(
            source_mesh, source_inv, list(occupied), ox, oy, oz, cell_len, bake_w, bake_h, bake_px, bake_uv_flat
        )
        if native_result is not None:
            mapped_count, cube_color_map = native_result
            _log(f"[Voxelator] Material map (native): {mapped_count} cells in {time.perf_counter() - native_start:.3f}s")
            if progress:
                progress.update(progress_end, progress_label)
            return mapped_count, cube_color_map

    min_v, max_v = _mesh_bounds(source_verts)
    loop_tris = list(source_mesh.loop_triangles)
    uv_layer = source_mesh.uv_layers.active
    uv_data = uv_layer.data if uv_layer else None
    loop_tris_by_poly = {}
    if uv_data or use_bake:
        for loop_tri in loop_tris:
            loop_tris_by_poly.setdefault(loop_tri.polygon_index, []).append(loop_tri)
    mat_source_cache = mat_source_cache if mat_source_cache is not None else {}
    image_cache = image_cache if image_cache is not None else {}
    bvh = None
    if loop_tris:
        try:
            if np is not None:
                vert_buf = np.empty(len(source_verts) * 3, dtype=np.float64)
                source_verts.foreach_get("co", vert_buf)
                verts_local = vert_buf.reshape(-1, 3).tolist()
                tri_buf = np.empty(len(loop_tris) * 3, dtype=np.int64)
                source_mesh.loop_triangles.foreach_get("vertices", tri_buf)
                tri_indices = tri_buf.reshape(-1, 3).tolist()
            else:
                verts_local = [v.co.copy() for v in source_verts]
                tri_indices = [tuple(loop_tri.vertices) for loop_tri in loop_tris]
            bvh = BVHTree.FromPolygons(verts_local, tri_indices, all_triangles=True)
        except Exception as exc:
            _log(f"[Voxelator] BVH material lookup fallback: {exc}")
    occ_list = list(occupied)
    n_occ = len(occ_list)
    step_occ = max(1, n_occ // 10) if n_occ else 1

    for i, (ix, iy, iz) in enumerate(occ_list):
        cube_loc = Vector((ox + ix * cell_len, oy + iy * cell_len, oz + iz * cell_len))
        local_loc = source_inv @ cube_loc
        result = False
        location = None
        poly = None
        loop_tri = None
        if bvh:
            nearest = bvh.find_nearest(local_loc)
            if nearest and nearest[2] is not None:
                location, normal, tri_index, distance = nearest
                if 0 <= tri_index < len(loop_tris):
                    loop_tri = loop_tris[tri_index]
                    if loop_tri.polygon_index < len(source_polys):
                        poly = source_polys[loop_tri.polygon_index]
                        result = True
        if not result:
            result, location, normal, poly_index = source.closest_point_on_mesh(local_loc)
            if result and poly_index < len(source_polys):
                poly = source_polys[poly_index]
        if result and poly is not None:
            color = None

            if use_bake:
                bake_uv = None
                if loop_tri is not None:
                    bake_uv = _uv_from_loop_tri_flat(location, loop_tri, bake_uv_flat, source_verts)
                else:
                    for candidate_tri in loop_tris_by_poly.get(poly.index, ()):
                        bake_uv = _uv_from_loop_tri_flat(location, candidate_tri, bake_uv_flat, source_verts)
                        if bake_uv is not None:
                            break
                if bake_uv is not None:
                    color = _sample_pixels_bilinear(bake_w, bake_h, bake_px, bake_uv)

            if color is None and poly.material_index < len(source_mats):
                mat = source_mats[poly.material_index]
                if mat:
                    source_info = _get_material_color_source(mat, mat_source_cache)
                    if source_info[0] == "solid":
                        color = source_info[1]
                    else:
                        uv = None
                        if uv_data:
                            if loop_tri is not None:
                                tri_uv = _uv_from_triangle(location, loop_tri, uv_data, source_verts)
                                if tri_uv is not None:
                                    uv = (tri_uv[0], tri_uv[1])
                            elif poly.loop_indices:
                                uv = _estimate_face_uv(location, poly, uv_data, source_loops, source_verts, loop_tris_by_poly)

                        if source_info[0] == "image":
                            sampled = _sample_image_bilinear(source_info[1], uv, image_cache) if uv is not None else None
                            color = sampled if sampled is not None else source_info[2]
                        elif source_info[0] == "node":
                            sample_ctx = {
                                "local": location,
                                "generated": _generated_coord(location, min_v, max_v),
                                "uv": uv,
                            }
                            evaluated = _eval_node_socket(source_info[1], sample_ctx, image_cache)
                            color = _rgba_tuple(evaluated, source_info[2])

            if color is not None:
                mapped_count += 1
                cube_color_map[(ix, iy, iz)] = color
        if ((i + 1) % step_occ) == 0 or (i + 1) == n_occ:
            _log(f"[Voxelator] Material map {i+1}/{n_occ}")
            _progress_range(progress, progress_start, progress_end, i + 1, n_occ, progress_label)

    return mapped_count, cube_color_map

class OBJECT_OT_voxelize(Operator):
    bl_label = "Voxelate"
    bl_idname = "object.voxelize"
    bl_description = "Converts any mesh into a voxelized mesh made up by cubes"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {'UNDO'}
    
    voxelizeResolution: bpy.props.IntProperty(
        name = "Voxel Resolution",
        default = 16,
        min = 1,
        max = 1080,
        description = "Maximum amount of cubes used per axis of mesh. *warning*: amounts higher than 32 can result in long load times during voxelization.",
    )
    
    separate_cubes: bpy.props.BoolProperty(
        name="Separate Cubes",
        description="Keep cubes as separate meshes inside the same object.",
        default = False
    )
    apply_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers",
        description="Voxelize the evaluated mesh with all modifiers applied",
        default=True,
    )
    bake_colors: bpy.props.BoolProperty(
        name="Bake Colors",
        description="Bake exact material base colors with Cycles (diffuse color pass) and sample voxel colors from the bake",
        default=True,
    )
    bake_resolution: bpy.props.IntProperty(
        name="Bake Resolution",
        description="Square resolution of the baked base color image",
        default=1024,
        min=64,
        max=8192,
    )
    reuse_bake: bpy.props.BoolProperty(
        name="Reuse Bake",
        description="Reuse the cached base color bake from a previous run in this session (used by the CLI when exporting multiple actions)",
        default=False,
    )
    rotation_offset_deg: bpy.props.FloatProperty(
        name="Rotation Offset Z",
        description="Additional Z-axis rotation offset in degrees applied before voxelization",
        default=0.0,
        soft_min=-360.0,
        soft_max=360.0,
        step=10,
    )
    animation_action: bpy.props.EnumProperty(
        name="Animation",
        description="Select an animation available in this project",
        items=_animation_items_for_object
    )
    export_animation: bpy.props.BoolProperty(
        name="Export Animation",
        description="Export selected animation to a single stacked slices PNG",
        default=False
    )
    frame_step: bpy.props.IntProperty(
        name="Frame Step",
        description="Use every Nth frame from selected animation",
        default=1,
        min=1
    )
    slices_only: bpy.props.BoolProperty(
        name="Slices Only",
        description="Only export voxel slices PNG and skip building the voxel mesh",
        default=True
    )
    slices_filepath: bpy.props.StringProperty(
        name="Slices PNG",
        description="Path to save the voxel slice spritesheet (.png)",
        subtype='FILE_PATH',
        default=""
    )
    see_preview: bpy.props.BoolProperty(
        name="See Preview",
        description="Open the exported static stacked PNG in a separate Blender preview window as a rotatable voxel mesh",
        default=False,
    )
    log_filepath: bpy.props.StringProperty(
        name="Log File",
        description="Path to save processing log (.log)",
        subtype='FILE_PATH',
        default=""
    )
    console_progress: bpy.props.BoolProperty(
        name="Console Progress",
        description="Print progress logs to console",
        default=False
    )
    
    @classmethod
    def poll(cls, context):
        return context.object.select_get() and context.object.type == 'MESH' or context.object.type == 'CURVE'
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "voxelizeResolution")
        layout.prop(self, "separate_cubes")
        layout.prop(self, "apply_modifiers")
        layout.prop(self, "bake_colors")
        if self.bake_colors:
            layout.prop(self, "bake_resolution")
        layout.prop(self, "rotation_offset_deg")
        layout.prop(self, "animation_action")
        layout.prop(self, "export_animation")
        if self.export_animation:
            layout.prop(self, "frame_step")
        layout.prop(self, "slices_only")
        layout.prop(self, "slices_filepath")
        if not self.export_animation:
            layout.prop(self, "see_preview")
        layout.prop(self, "log_filepath")
    
    def execute(self, context):
        total_start = time.perf_counter()
        stage_start = total_start
        progress = _ProgressReporter(context)
        progress.begin("Starting")

        global LOG_FILE
        global LOG_TO_STDOUT
        source = context.object
        source_name = source.name

        LOG_TO_STDOUT = bool(self.console_progress)

        log_path = self.log_filepath.strip()
        if not log_path:
            LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voxelator.log")
        else:
            if not log_path.lower().endswith(".log"):
                log_path = log_path + ".log"
            LOG_FILE = bpy.path.abspath(log_path)

        _log(f"[Voxelator] Start: {source_name}")
        _log(f"[Voxelator] res: {self.voxelizeResolution} separate_cubes: {self.separate_cubes}")
        _log(f"[Voxelator] apply_modifiers: {self.apply_modifiers}")
        _log(f"[Voxelator] bake_colors: {self.bake_colors} bake_resolution: {self.bake_resolution}")
        _log(f"[Voxelator] rotation_offset_deg: {self.rotation_offset_deg}")
        _log(f"[Voxelator] animation: {self.animation_action}")
        _log(f"[Voxelator] export_animation: {self.export_animation} frame_step: {self.frame_step}")
        _log(f"[Voxelator] slices_only: {self.slices_only}")
        _log(f"[Voxelator] see_preview: {self.see_preview}")
        _log(f"[Voxelator] slices path: {self.slices_filepath or '(default)'}")
        _log(f"[Voxelator] log path: {LOG_FILE}")

        save_path = self.slices_filepath.strip()
        if not save_path:
            save_path = bpy.path.abspath(f"//{source_name}_voxel_slices_{self.voxelizeResolution}.png")
        elif not save_path.lower().endswith(".png"):
            save_path = save_path + ".png"

        depsgraph = context.evaluated_depsgraph_get()
        rot_rad = math.radians(float(self.rotation_offset_deg))
        rot_offset_matrix = Matrix.Rotation(rot_rad, 4, 'Z')

        try:
            if self.export_animation:
                return self._execute_animation(context, source, source_name, depsgraph, rot_offset_matrix, save_path, total_start, progress)
            return self._execute_static(context, source, source_name, depsgraph, rot_offset_matrix, save_path, total_start, stage_start, progress)
        finally:
            progress.end()

    def _execute_animation(self, context, source, source_name, depsgraph, rot_offset_matrix, save_path, total_start, progress):
        if self.export_animation:
            if self.animation_action in {"", "NONE"}:
                _log("[Voxelator] Aborted: no animation selected for export")
                self.report({'ERROR'}, "Voxelator: no animation selected")
                return {'CANCELLED'}

            action = bpy.data.actions.get(self.animation_action)
            if not action:
                _log(f"[Voxelator] Aborted: animation not found: {self.animation_action}")
                self.report({'ERROR'}, f"Voxelator: animation not found ({self.animation_action})")
                return {'CANCELLED'}

            anim_owner = _get_animation_owner(source)
            scene = context.scene
            original_frame = scene.frame_current
            created_anim_data = False
            prev_action = None

            if not anim_owner.animation_data:
                anim_owner.animation_data_create()
                created_anim_data = True
            prev_action = anim_owner.animation_data.action
            anim_owner.animation_data.action = action

            frame_start = int(math.floor(action.frame_range[0]))
            frame_end = int(math.ceil(action.frame_range[1]))
            frame_step = max(1, int(self.frame_step))
            frames = list(range(frame_start, frame_end, frame_step))
            if not frames:
                frames = [frame_start]

            _log(f"[Voxelator] Animation export owner: {anim_owner.name}")
            _log(f"[Voxelator] Animation range: {frame_start}..{frame_end} (last frame excluded for looping) step={frame_step} sampled={len(frames)}")

            try:
                progress.update(5.0, "Scanning animation bounds")
                bounds_start = time.perf_counter()
                min_x = float('inf')
                min_y = float('inf')
                min_z = float('inf')
                max_x = float('-inf')
                max_y = float('-inf')
                max_z = float('-inf')

                for i, frame in enumerate(frames):
                    scene.frame_set(frame)
                    eval_mesh = _mesh_from_source(source, depsgraph, self.apply_modifiers)
                    processing_matrix = source.matrix_world @ rot_offset_matrix
                    bounds = _world_bounds(eval_mesh, processing_matrix)
                    bpy.data.meshes.remove(eval_mesh)
                    if bounds is None:
                        continue

                    (bmin_x, bmin_y, bmin_z), (bmax_x, bmax_y, bmax_z) = bounds
                    min_x = min(min_x, bmin_x)
                    min_y = min(min_y, bmin_y)
                    min_z = min(min_z, bmin_z)
                    max_x = max(max_x, bmax_x)
                    max_y = max(max_y, bmax_y)
                    max_z = max(max_z, bmax_z)
                    _log(f"[Voxelator] Animation bounds {i+1}/{len(frames)} frame={frame}")
                    _progress_range(progress, 5.0, 15.0, i + 1, len(frames), "Scanning animation bounds")

                if min_x == float('inf'):
                    _log("[Voxelator] Aborted: no vertices found across sampled animation frames")
                    self.report({'ERROR'}, "Voxelator: no vertices found in sampled animation")
                    return {'CANCELLED'}

                span_x = max_x - min_x
                span_y = max_y - min_y
                span_z = max_z - min_z
                max_span = max(span_x, span_y, span_z)

                cube_size = max_span / (self.voxelizeResolution * 2) if self.voxelizeResolution else 0.5
                cell_len = cube_size * 2
                grid_min_x = min_x
                grid_min_y = min_y
                grid_min_z = min_z

                eps = cell_len * 1e-6
                tol = max_span * 1e-6 if max_span > 0.0 else 0.0

                if abs(span_x - max_span) <= tol:
                    dx = max(1, int(self.voxelizeResolution))
                else:
                    dx = max(1, int(math.ceil((span_x + eps) / cell_len)))

                if abs(span_y - max_span) <= tol:
                    dy = max(1, int(self.voxelizeResolution))
                else:
                    dy = max(1, int(math.ceil((span_y + eps) / cell_len)))

                if abs(span_z - max_span) <= tol:
                    dz = max(1, int(self.voxelizeResolution))
                else:
                    dz = max(1, int(math.ceil((span_z + eps) / cell_len)))

                center_x = (min_x + max_x) * 0.5
                center_y = (min_y + max_y) * 0.5
                center_z = (min_z + max_z) * 0.5
                grid_min_x = center_x - (dx * cell_len) * 0.5
                grid_min_y = center_y - (dy * cell_len) * 0.5
                grid_min_z = center_z - (dz * cell_len) * 0.5

                ox = grid_min_x + 0.5 * cell_len
                oy = grid_min_y + 0.5 * cell_len
                oz = grid_min_z + 0.5 * cell_len

                _log(f"[Voxelator][Timing] Animation bounds prepass: {time.perf_counter() - bounds_start:.3f}s")
                _log(f"[Voxelator] Global animation grid: {dx}x{dy}x{dz}")
                _log(f"[Voxelator] cube_size={cube_size:.6f} cell_len={cell_len:.6f}")
                _log(f"[Voxelator] Grid center: ({center_x:.6f}, {center_y:.6f}, {center_z:.6f})")

                bake_pixels = None
                bake_uv_flat = None
                if self.bake_colors:
                    progress.update(15.0, "Baking base colors")
                    eff_bake_res = _effective_bake_resolution(self.bake_resolution, self.voxelizeResolution)
                    bake_key = (source_name, bool(self.apply_modifiers), int(eff_bake_res))
                    cached_bake = _BAKE_CACHE.get("entry")
                    if self.reuse_bake and cached_bake and cached_bake["key"] == bake_key:
                        bake_pixels = cached_bake["pixels"]
                        bake_uv_flat = cached_bake["uv_flat"]
                        _log("[Voxelator] Reusing cached base color bake")
                    else:
                        scene.frame_set(frames[0])
                        bake_mesh = _mesh_from_source(source, depsgraph, self.apply_modifiers)
                        bake_obj = bpy.data.objects.new(source_name + "_voxel_bake", bake_mesh)
                        context.collection.objects.link(bake_obj)
                        try:
                            bake_pixels, bake_uv_flat = _bake_base_color_image(context, bake_obj, eff_bake_res)
                        finally:
                            bpy.data.objects.remove(bake_obj, do_unlink=True)
                            bpy.data.meshes.remove(bake_mesh)
                        if bake_pixels is not None:
                            _BAKE_CACHE["entry"] = {"key": bake_key, "pixels": bake_pixels, "uv_flat": bake_uv_flat}
                progress.update(25.0, "Processing animation frames")

                frame_color_maps = []
                anim_mat_source_cache = {}
                anim_image_cache = {}
                anim_proc_start = time.perf_counter()
                for i, frame in enumerate(frames):
                    frame_start_pct = 25.0 + 60.0 * (i / len(frames))
                    frame_end_pct = 25.0 + 60.0 * ((i + 1) / len(frames))
                    progress.update(frame_start_pct, f"Processing frame {i+1}/{len(frames)}")
                    scene.frame_set(frame)
                    eval_mesh = _mesh_from_source(source, depsgraph, self.apply_modifiers)
                    processing_matrix = source.matrix_world @ rot_offset_matrix
                    occupied = _build_occupied_cells_from_mesh(eval_mesh, processing_matrix, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz)
                    _log(f"[Voxelator] Frame {frame}: occupied={len(occupied)}")

                    color_source = bpy.data.objects.new(source_name + "_voxel_color_source", eval_mesh)
                    try:
                        mapped_count, cube_color_map = _build_cube_maps(
                            color_source,
                            occupied,
                            ox,
                            oy,
                            oz,
                            cell_len,
                            world_to_source_matrix=processing_matrix.inverted(),
                            mat_source_cache=anim_mat_source_cache,
                            image_cache=anim_image_cache,
                            bake_data=(bake_pixels, bake_uv_flat) if bake_pixels is not None else None,
                            progress=progress,
                            progress_start=frame_start_pct + (frame_end_pct - frame_start_pct) * 0.35,
                            progress_end=frame_end_pct,
                            progress_label=f"Mapping frame {i+1}/{len(frames)} colors",
                        )
                    finally:
                        bpy.data.objects.remove(color_source, do_unlink=True)
                        bpy.data.meshes.remove(eval_mesh)
                    frame_color_maps.append(cube_color_map)
                    _log(f"[Voxelator] Frame {frame}: mapped={mapped_count} colorized={len(cube_color_map)} ({i+1}/{len(frames)})")
                    progress.update(frame_end_pct, f"Finished frame {i+1}/{len(frames)}")

                _log(f"[Voxelator][Timing] Animation frame processing: {time.perf_counter() - anim_proc_start:.3f}s")

                _log(f"[Voxelator] Saving animation spritesheet to: {save_path}")
                progress.update(85.0, "Saving animation spritesheet")
                sprite_start = time.perf_counter()
                _save_voxel_animation_spritesheet(frame_color_maps, dx, dy, dz, save_path, self.voxelizeResolution, progress=progress, progress_start=85.0, progress_end=100.0)
                _log(f"[Voxelator][Timing] Animation spritesheet: {time.perf_counter() - sprite_start:.3f}s")
            finally:
                scene.frame_set(original_frame)
                if anim_owner.animation_data:
                    anim_owner.animation_data.action = prev_action
                if created_anim_data and anim_owner.animation_data and anim_owner.animation_data.action is None and not anim_owner.animation_data.nla_tracks:
                    anim_owner.animation_data_clear()

            _log("[Voxelator] Animation mode: PNG-only export complete")
            _log(f"[Voxelator][Timing] Total: {time.perf_counter() - total_start:.3f}s")
            _log("[Voxelator] Finished")
            self.report({'INFO'}, f"Voxelator completed animation PNG: {os.path.basename(save_path)}")
            return {'FINISHED'}

    def _execute_static(self, context, source, source_name, depsgraph, rot_offset_matrix, save_path, total_start, stage_start, progress):
        progress.update(5.0, "Preparing mesh")
        target_mesh = _mesh_from_source(source, depsgraph, self.apply_modifiers)
        target = bpy.data.objects.new(source_name + "_voxelized", target_mesh)
        processing_matrix = source.matrix_world @ rot_offset_matrix
        target.matrix_world = processing_matrix
        context.collection.objects.link(target)
        if not self.slices_only:
            source.hide_set(True)
        _log(f"[Voxelator] Built eval mesh object: {target.name}")
        _log(f"[Voxelator] Target dims: {target.dimensions[:]}")

        bounds = _world_bounds(target.data, target.matrix_world)
        if bounds is None:
            bpy.data.objects.remove(target, do_unlink=True)
            _log("[Voxelator] Aborted: target has no vertices")
            self.report({'ERROR'}, "Voxelator: target has no vertices")
            return {'CANCELLED'}
        (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds

        span_x = max_x - min_x
        span_y = max_y - min_y
        span_z = max_z - min_z
        max_span = max(span_x, span_y, span_z)

        cube_size = max_span / (self.voxelizeResolution * 2) if self.voxelizeResolution else 0.5
        cell_len = cube_size * 2
        _log(f"[Voxelator] cube_size={cube_size:.6f} cell_len={cell_len:.6f}")
        _log(f"[Voxelator][Timing] Setup: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        grid_min_x = min_x
        grid_min_y = min_y
        grid_min_z = min_z

        eps = cell_len * 1e-6
        tol = max_span * 1e-6 if max_span > 0.0 else 0.0

        if abs(span_x - max_span) <= tol:
            dx = max(1, int(self.voxelizeResolution))
        else:
            dx = max(1, int(math.ceil((span_x + eps) / cell_len)))

        if abs(span_y - max_span) <= tol:
            dy = max(1, int(self.voxelizeResolution))
        else:
            dy = max(1, int(math.ceil((span_y + eps) / cell_len)))

        if abs(span_z - max_span) <= tol:
            dz = max(1, int(self.voxelizeResolution))
        else:
            dz = max(1, int(math.ceil((span_z + eps) / cell_len)))

        center_x = (min_x + max_x) * 0.5
        center_y = (min_y + max_y) * 0.5
        center_z = (min_z + max_z) * 0.5
        grid_min_x = center_x - (dx * cell_len) * 0.5
        grid_min_y = center_y - (dy * cell_len) * 0.5
        grid_min_z = center_z - (dz * cell_len) * 0.5

        ox = grid_min_x + 0.5 * cell_len
        oy = grid_min_y + 0.5 * cell_len
        oz = grid_min_z + 0.5 * cell_len
        _log(f"[Voxelator] Grid center: ({center_x:.6f}, {center_y:.6f}, {center_z:.6f})")

        progress.update(10.0, "Voxelizing surface")
        surface_start = time.perf_counter()
        occupied = _build_occupied_cells_from_mesh(target.data, target.matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz)
        _log(f"[Voxelator][Timing] Surface voxelize: {time.perf_counter() - surface_start:.3f}s")
        progress.update(20.0, "Surface voxelized")
        stage_start = time.perf_counter()

        _log(f"[Voxelator] Grid: {dx}x{dy}x{dz}")
        _log(f"[Voxelator] Occupied cells: {len(occupied)}")
        _log(f"[Voxelator][Timing] Occupancy bookkeeping: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        bake_pixels = None
        bake_uv_flat = None
        if self.bake_colors:
            progress.update(25.0, "Baking base colors")
            bake_pixels, bake_uv_flat = _bake_base_color_image(
                context, target, _effective_bake_resolution(self.bake_resolution, self.voxelizeResolution)
            )
        _log(f"[Voxelator][Timing] Bake stage (unwrap+bake+readback): {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()
        progress.update(45.0, "Mapping voxel colors")

        mapped_count, cube_color_map = _build_cube_maps(
            target,
            occupied,
            ox,
            oy,
            oz,
            cell_len,
            world_to_source_matrix=processing_matrix.inverted(),
            bake_data=(bake_pixels, bake_uv_flat) if bake_pixels is not None else None,
            progress=progress,
            progress_start=45.0,
            progress_end=85.0,
            progress_label="Mapping voxel colors",
        )
        _log(f"[Voxelator] Material mapped: {mapped_count} colorized: {len(cube_color_map)}")
        _log(f"[Voxelator][Timing] Material map: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        _log(f"[Voxelator] Saving spritesheet to: {save_path}")
        progress.update(85.0, "Saving spritesheet")
        _save_voxel_spritesheet(dx, dy, dz, save_path, cube_color_map, self.voxelizeResolution, progress=progress, progress_start=85.0, progress_end=95.0)
        _log(f"[Voxelator][Timing] Spritesheet: {time.perf_counter() - stage_start:.3f}s")
        if self.see_preview:
            _launch_preview_process(bpy.path.abspath(save_path))

        if self.slices_only:
            bpy.data.objects.remove(target, do_unlink=True)
            _log("[Voxelator] Slices-only mode: skipped voxel mesh build")
            _log(f"[Voxelator][Timing] Total: {time.perf_counter() - total_start:.3f}s")
            _log("[Voxelator] Finished")
            progress.update(100.0, "Finished")
            self.report({'INFO'}, f"Voxelator completed PNG: {os.path.basename(save_path)}")
            return {'FINISHED'}

        stage_start = time.perf_counter()
        progress.update(95.0, "Building voxel mesh")

        verts, faces, face_cells = _build_voxel_mesh_data(occupied, ox, oy, oz, cell_len, self.separate_cubes)
        mesh_name = source_name + "_voxel_mesh"
        mesh = bpy.data.meshes.new(mesh_name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(mesh_name, mesh)
        context.collection.objects.link(obj)

        bpy.data.objects.remove(target, do_unlink=True)
        _log("[Voxelator] Removed temp objects")
        _log(f"[Voxelator] New object: {obj.name}")
        _log(f"[Voxelator][Timing] Mesh build: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        applied_faces = _apply_voxel_color_attribute(obj, face_cells, cube_color_map)
        _log(f"[Voxelator] Voxel colors applied: {applied_faces}")
        _log(f"[Voxelator][Timing] Voxel color attributes: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        max_dim = max(obj.dimensions)
        if max_dim > 0:
            resize_value = 1 / (max_dim / self.voxelizeResolution)
            for v in obj.data.vertices:
                v.co *= resize_value
            obj.data.update()
            _log("[Voxelator] Resized to 1m cubes")

        bb = [v.co.copy() for v in obj.data.vertices]
        if bb:
            min_v = Vector((min(v.x for v in bb), min(v.y for v in bb), min(v.z for v in bb)))
            max_v = Vector((max(v.x for v in bb), max(v.y for v in bb), max(v.z for v in bb)))
            center = (min_v + max_v) * 0.5
            obj.data.transform(Matrix.Translation(-center))
            obj.data.update()
        obj.location = (0.0, 0.0, 0.0)
        _log("[Voxelator] Centered at origin")
        _log(f"[Voxelator][Timing] Finalize: {time.perf_counter() - stage_start:.3f}s")
        _log(f"[Voxelator][Timing] Total: {time.perf_counter() - total_start:.3f}s")
        _log("[Voxelator] Finished")
        progress.update(100.0, "Finished")
        self.report({'INFO'}, f"Voxelator completed mesh + PNG: {os.path.basename(save_path)}")
        return {'FINISHED'}

def menu_func(self, context):
    self.layout.operator(OBJECT_OT_voxelize.bl_idname)
    
def register():
    bpy.utils.register_class(OBJECT_OT_voxelize)
    bpy.types.VIEW3D_MT_object.append(menu_func)
    
def unregister():
    bpy.utils.unregister_class(OBJECT_OT_voxelize)
    bpy.types.VIEW3D_MT_object.remove(menu_func)
    
if __name__ == "__main__":
    register()
    
