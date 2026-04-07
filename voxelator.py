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
import os
import time
import math
from collections import deque
from mathutils import Vector, Matrix
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

def _log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass

def _get_color_from_material(mat):
    if not mat or not getattr(mat, "use_nodes", False):
        return (1.0, 1.0, 1.0, 1.0)
    for node in mat.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            base_color = node.inputs['Base Color']
            if base_color.is_linked:
                linked_node = base_color.links[0].from_node
                if linked_node.type == 'TEX_IMAGE' and linked_node.image:
                    return (1.0, 0.5, 0.5, 1.0)
            else:
                return base_color.default_value[:]
        if node.type == 'BSDF_TOON':
            base_color = node.inputs['Color']
            if base_color.is_linked:
                linked_node = base_color.links[0].from_node
                if linked_node.type == 'TEX_IMAGE' and linked_node.image:
                    return (1.0, 0.5, 0.5, 1.0)
            else:
                return base_color.default_value[:]
    return (1.0, 1.0, 1.0, 1.0)

def _build_layer_color_map(dx, dy, dz, cube_mat_map):
    layers = [{} for _ in range(dz)]
    cache = {}
    for (ix, iy, iz), mat in cube_mat_map.items():
        if 0 <= ix < dx and 0 <= iy < dy and 0 <= iz < dz and mat:
            key = mat.name
            color = cache.get(key)
            if color is None:
                color = _get_color_from_material(mat)
                cache[key] = color
            layers[iz][(ix, iy)] = color
    return layers

def _render_layers_into_pixels(px, width, height, layers, dx, dy, dz, row_count=1, row_index=0, align_left=False):
    tile = max(dx, dy)
    off_x = 0 if align_left else (tile - dx) // 2
    off_y = (tile - dy) // 2
    row_bottom = (row_count - 1 - row_index) * tile
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

def _save_voxel_spritesheet(dx, dy, dz, filepath, cube_mat_map):
    layers = _build_layer_color_map(dx, dy, dz, cube_mat_map)

    cube_count = len(cube_mat_map)
    _log(f"[Voxelator] Building spritesheet from {cube_count} cubes; grid: {dx} {dy} {dz}")

    tile = max(dx, dy)
    width = tile * dz
    height = tile
    abs_path = bpy.path.abspath(filepath)
    base = os.path.splitext(os.path.basename(abs_path))[0]
    img = bpy.data.images.new(f"voxel_slices_{base}", width=width, height=height, alpha=True, float_buffer=False)
    px = [0.0] * (width * height * 4)
    _log(f"[Voxelator] Spritesheet dimensions: {width} x {height}")
    _render_layers_into_pixels(px, width, height, layers, dx, dy, dz)
    img.pixels = px
    img.filepath_raw = abs_path
    img.file_format = 'PNG'
    img.save()
    _log(f"[Voxelator] Saved spritesheet: {abs_path}")

def _save_voxel_animation_spritesheet(frame_cube_maps, dx, dy, dz, filepath):
    frame_count = len(frame_cube_maps)
    tile = max(dx, dy)
    width = tile * dz
    height = tile * frame_count
    abs_path = bpy.path.abspath(filepath)
    base = os.path.splitext(os.path.basename(abs_path))[0]
    img = bpy.data.images.new(f"voxel_anim_slices_{base}", width=width, height=height, alpha=True, float_buffer=False)
    px = [0.0] * (width * height * 4)

    _log(f"[Voxelator] Building animation spritesheet frames={frame_count} grid={dx} {dy} {dz}")
    _log(f"[Voxelator] Animation spritesheet dimensions: {width} x {height}")

    for i, cube_mat_map in enumerate(frame_cube_maps):
        layers = _build_layer_color_map(dx, dy, dz, cube_mat_map)
        _render_layers_into_pixels(px, width, height, layers, dx, dy, dz, row_count=frame_count, row_index=i, align_left=True)
        _log(f"[Voxelator] Animation row {i+1}/{frame_count}")

    img.pixels = px
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

def _flood_fill_outside(dx, dy, dz, shell):
    outside = set()
    q = deque()

    def try_push(ix, iy, iz):
        cell = (ix, iy, iz)
        if cell in shell or cell in outside:
            return
        outside.add(cell)
        q.append(cell)

    for ix in range(dx):
        for iy in range(dy):
            try_push(ix, iy, 0)
            try_push(ix, iy, dz - 1)
    for ix in range(dx):
        for iz in range(dz):
            try_push(ix, 0, iz)
            try_push(ix, dy - 1, iz)
    for iy in range(dy):
        for iz in range(dz):
            try_push(0, iy, iz)
            try_push(dx - 1, iy, iz)

    neigh = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    while q:
        ix, iy, iz = q.popleft()
        for nx, ny, nz in neigh:
            tx = ix + nx
            ty = iy + ny
            tz = iz + nz
            if 0 <= tx < dx and 0 <= ty < dy and 0 <= tz < dz:
                cell = (tx, ty, tz)
                if cell not in shell and cell not in outside:
                    outside.add(cell)
                    q.append(cell)

    return outside

def _build_occupied_cells_from_mesh(mesh, matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz, fill_volume):
    mesh.calc_loop_triangles()
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

    if not fill_volume:
        return shell

    outside = _flood_fill_outside(dx, dy, dz, shell)
    occupied = set(shell)
    for ix in range(dx):
        for iy in range(dy):
            for iz in range(dz):
                cell = (ix, iy, iz)
                if cell not in outside:
                    occupied.add(cell)
    _log(f"[Voxelator] Volume fill: shell={len(shell)} outside={len(outside)} total={len(occupied)}")
    return occupied

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

def _build_cube_mat_map(source, occupied, ox, oy, oz, cell_len):
    cube_mat_map = {}
    source_inv = source.matrix_world.inverted()
    source_polys = source.data.polygons
    source_mats = source.data.materials
    occ_list = sorted(occupied)
    n_occ = len(occ_list)
    step_occ = max(1, n_occ // 10) if n_occ else 1

    for i, (ix, iy, iz) in enumerate(occ_list):
        cube_loc = Vector((ox + ix * cell_len, oy + iy * cell_len, oz + iz * cell_len))
        result, location, normal, poly_index = source.closest_point_on_mesh(source_inv @ cube_loc)
        if result and poly_index < len(source_polys):
            poly = source_polys[poly_index]
            if poly.material_index < len(source_mats):
                mat = source_mats[poly.material_index]
                if mat:
                    cube_mat_map[(ix, iy, iz)] = mat
        if ((i + 1) % step_occ) == 0 or (i + 1) == n_occ:
            _log(f"[Voxelator] Material map {i+1}/{n_occ}")

    return cube_mat_map

class OBJECT_OT_voxelize(Operator):
    bl_label = "Voxelate"
    bl_idname = "object.voxelize"
    bl_description = "Converts any mesh into a voxelized mesh made up by cubes"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {'REGISTER', 'UNDO'}
    
    voxelizeResolution: bpy.props.IntProperty(
        name = "Voxel Resolution",
        default = 16,
        min = 1,
        max = 250,
        description = "Maximum amount of cubes used per axis of mesh. *warning*: amounts higher than 32 can result in long load times during voxelization.",
    )
    
    fill_volume: bpy.props.BoolProperty(
        name="Fill Volume",
        description="Fill the inside of the voxelized mesh with cubes as well.",
        default = False
    )
    separate_cubes: bpy.props.BoolProperty(
        name="Separate Cubes",
        description="Keep cubes as separate meshes inside the same object.",
        default = False
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
    log_filepath: bpy.props.StringProperty(
        name="Log File",
        description="Path to save processing log (.log)",
        subtype='FILE_PATH',
        default=""
    )
    
    @classmethod
    def poll(cls, context):
        return context.object.select_get() and context.object.type == 'MESH' or context.object.type == 'CURVE'
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "voxelizeResolution")
        layout.prop(self, "fill_volume")
        layout.prop(self, "separate_cubes")
        layout.prop(self, "animation_action")
        layout.prop(self, "export_animation")
        if self.export_animation:
            layout.prop(self, "frame_step")
        layout.prop(self, "slices_only")
        layout.prop(self, "slices_filepath")
        layout.prop(self, "log_filepath")
    
    def execute(self, context):
        total_start = time.perf_counter()
        stage_start = total_start

        global LOG_FILE
        source = context.object
        source_name = source.name

        log_path = self.log_filepath.strip()
        if not log_path:
            LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voxelator.log")
        else:
            if not log_path.lower().endswith(".log"):
                log_path = log_path + ".log"
            LOG_FILE = bpy.path.abspath(log_path)

        _log(f"[Voxelator] Start: {source_name}")
        _log(f"[Voxelator] res: {self.voxelizeResolution} fill_volume: {self.fill_volume} separate_cubes: {self.separate_cubes}")
        _log(f"[Voxelator] animation: {self.animation_action}")
        _log(f"[Voxelator] export_animation: {self.export_animation} frame_step: {self.frame_step}")
        _log(f"[Voxelator] slices_only: {self.slices_only}")
        _log(f"[Voxelator] slices path: {self.slices_filepath or '(default)'}")
        _log(f"[Voxelator] log path: {LOG_FILE}")

        save_path = self.slices_filepath.strip()
        if not save_path:
            save_path = bpy.path.abspath(f"//{source_name}_voxel_slices_{self.voxelizeResolution}.png")
        elif not save_path.lower().endswith(".png"):
            save_path = save_path + ".png"

        depsgraph = context.evaluated_depsgraph_get()

        if self.export_animation:
            if self.animation_action in {"", "NONE"}:
                _log("[Voxelator] Aborted: no animation selected for export")
                return {'CANCELLED'}

            action = bpy.data.actions.get(self.animation_action)
            if not action:
                _log(f"[Voxelator] Aborted: animation not found: {self.animation_action}")
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
            frames = list(range(frame_start, frame_end + 1, frame_step))
            if not frames:
                frames = [frame_start]
            elif frames[-1] != frame_end:
                frames.append(frame_end)

            _log(f"[Voxelator] Animation export owner: {anim_owner.name}")
            _log(f"[Voxelator] Animation range: {frame_start}..{frame_end} step={frame_step} sampled={len(frames)}")

            try:
                bounds_start = time.perf_counter()
                min_x = float('inf')
                min_y = float('inf')
                min_z = float('inf')
                max_x = float('-inf')
                max_y = float('-inf')
                max_z = float('-inf')

                for i, frame in enumerate(frames):
                    scene.frame_set(frame)
                    source_eval = source.evaluated_get(depsgraph)
                    eval_mesh = bpy.data.meshes.new_from_object(source_eval, preserve_all_data_layers=True, depsgraph=depsgraph)
                    verts_world = [source.matrix_world @ v.co for v in eval_mesh.vertices]
                    bpy.data.meshes.remove(eval_mesh)
                    if not verts_world:
                        continue

                    min_x = min(min_x, min(v.x for v in verts_world))
                    min_y = min(min_y, min(v.y for v in verts_world))
                    min_z = min(min_z, min(v.z for v in verts_world))
                    max_x = max(max_x, max(v.x for v in verts_world))
                    max_y = max(max_y, max(v.y for v in verts_world))
                    max_z = max(max_z, max(v.z for v in verts_world))
                    _log(f"[Voxelator] Animation bounds {i+1}/{len(frames)} frame={frame}")

                if min_x == float('inf'):
                    _log("[Voxelator] Aborted: no vertices found across sampled animation frames")
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

                ox = grid_min_x + 0.5 * cell_len
                oy = grid_min_y + 0.5 * cell_len
                oz = grid_min_z + 0.5 * cell_len

                _log(f"[Voxelator][Timing] Animation bounds prepass: {time.perf_counter() - bounds_start:.3f}s")
                _log(f"[Voxelator] Global animation grid: {dx}x{dy}x{dz}")
                _log(f"[Voxelator] cube_size={cube_size:.6f} cell_len={cell_len:.6f}")

                frame_cube_maps = []
                anim_proc_start = time.perf_counter()
                for i, frame in enumerate(frames):
                    scene.frame_set(frame)
                    source_eval = source.evaluated_get(depsgraph)
                    eval_mesh = bpy.data.meshes.new_from_object(source_eval, preserve_all_data_layers=True, depsgraph=depsgraph)
                    occupied = _build_occupied_cells_from_mesh(eval_mesh, source.matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz, self.fill_volume)
                    bpy.data.meshes.remove(eval_mesh)
                    _log(f"[Voxelator] Frame {frame}: occupied={len(occupied)}")

                    cube_mat_map = _build_cube_mat_map(source, occupied, ox, oy, oz, cell_len)
                    frame_cube_maps.append(cube_mat_map)
                    _log(f"[Voxelator] Frame {frame}: mapped={len(cube_mat_map)} ({i+1}/{len(frames)})")

                _log(f"[Voxelator][Timing] Animation frame processing: {time.perf_counter() - anim_proc_start:.3f}s")

                _log(f"[Voxelator] Saving animation spritesheet to: {save_path}")
                sprite_start = time.perf_counter()
                _save_voxel_animation_spritesheet(frame_cube_maps, dx, dy, dz, save_path)
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
            return {'FINISHED'}
        source_eval = source.evaluated_get(depsgraph)
        target_mesh = bpy.data.meshes.new_from_object(source_eval, preserve_all_data_layers=True, depsgraph=depsgraph)
        target = bpy.data.objects.new(source_name + "_voxelized", target_mesh)
        target.matrix_world = source.matrix_world.copy()
        context.collection.objects.link(target)
        if not self.slices_only:
            source.hide_set(True)
        _log(f"[Voxelator] Built eval mesh object: {target.name}")
        _log(f"[Voxelator] Target dims: {target.dimensions[:]}")

        verts_world = [target.matrix_world @ v.co for v in target.data.vertices]
        if not verts_world:
            bpy.data.objects.remove(target, do_unlink=True)
            _log("[Voxelator] Aborted: target has no vertices")
            return {'CANCELLED'}
        min_x = min(v.x for v in verts_world)
        min_y = min(v.y for v in verts_world)
        min_z = min(v.z for v in verts_world)
        max_x = max(v.x for v in verts_world)
        max_y = max(v.y for v in verts_world)
        max_z = max(v.z for v in verts_world)

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

        ox = grid_min_x + 0.5 * cell_len
        oy = grid_min_y + 0.5 * cell_len
        oz = grid_min_z + 0.5 * cell_len

        surface_start = time.perf_counter()
        occupied = _build_occupied_cells_from_mesh(target.data, target.matrix_world, cell_len, grid_min_x, grid_min_y, grid_min_z, dx, dy, dz, self.fill_volume)
        _log(f"[Voxelator][Timing] Surface/volume voxelize: {time.perf_counter() - surface_start:.3f}s")
        stage_start = time.perf_counter()

        _log(f"[Voxelator] Grid: {dx}x{dy}x{dz}")
        _log(f"[Voxelator] Occupied cells: {len(occupied)}")
        _log(f"[Voxelator][Timing] Occupancy bookkeeping: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        cube_mat_map = _build_cube_mat_map(source, occupied, ox, oy, oz, cell_len)
        _log(f"[Voxelator][Timing] Material map: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        _log(f"[Voxelator] Saving spritesheet to: {save_path}")
        _save_voxel_spritesheet(dx, dy, dz, save_path, cube_mat_map)
        _log(f"[Voxelator][Timing] Spritesheet: {time.perf_counter() - stage_start:.3f}s")

        if self.slices_only:
            bpy.data.objects.remove(target, do_unlink=True)
            _log("[Voxelator] Slices-only mode: skipped voxel mesh build")
            _log(f"[Voxelator][Timing] Total: {time.perf_counter() - total_start:.3f}s")
            _log("[Voxelator] Finished")
            return {'FINISHED'}

        stage_start = time.perf_counter()

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

        obj.data.materials.clear()
        for mat_slot in source.material_slots:
            if mat_slot.material:
                obj.data.materials.append(mat_slot.material)
        _log(f"[Voxelator] Materials appended: {sum(1 for s in source.material_slots if s.material)}")

        mat_name_to_idx = {m.name: i for i, m in enumerate(obj.data.materials)}
        polys = obj.data.polygons
        total_p = len(polys)
        step_p = max(1, total_p // 10) if total_p else 1
        for pi, poly in enumerate(polys):
            if pi >= len(face_cells):
                break
            mat = cube_mat_map.get(face_cells[pi])
            if mat:
                idx = mat_name_to_idx.get(mat.name, -1)
                if idx != -1:
                    poly.material_index = idx
            if ((pi + 1) % step_p) == 0 or (pi + 1) == total_p:
                _log(f"[Voxelator] Face material assign {pi+1}/{total_p}")

        mod = obj.modifiers.new(name='DataTransfer', type='DATA_TRANSFER')
        mod.use_loop_data = True
        mod.data_types_loops = {'UV'}
        mod.loop_mapping = 'POLYINTERP_NEAREST'
        mod.object = source
        bpy.ops.object.datalayout_transfer(modifier=mod.name)
        bpy.ops.object.modifier_apply(modifier=mod.name)
        _log("[Voxelator] UV transfer applied")
        _log(f"[Voxelator][Timing] Materials + UV transfer: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        max_dim = max(obj.dimensions)
        if max_dim > 0:
            resize_value = 1 / (max_dim / self.voxelizeResolution)
            for v in obj.data.vertices:
                v.co *= resize_value
            obj.data.update()
            _log("[Voxelator] Resized to 1m cubes")

        _log("[Voxelator] Shrinking UVs...")
        uv_layer = obj.data.uv_layers.active
        if uv_layer:
            mesh = obj.data
            polys = mesh.polygons
            loops = mesh.loops
            total_uv_polys = len(polys)
            total_loops = len(loops)
            step_uv = max(1, total_uv_polys // 10) if total_uv_polys else 1

            uv_flat = [0.0] * (total_loops * 2)
            uv_layer.data.foreach_get("uv", uv_flat)

            loop_starts = [0] * total_uv_polys
            loop_totals = [0] * total_uv_polys
            polys.foreach_get("loop_start", loop_starts)
            polys.foreach_get("loop_total", loop_totals)

            for pi in range(total_uv_polys):
                start = loop_starts[pi]
                count = loop_totals[pi]
                if count <= 0:
                    continue

                sum_u = 0.0
                sum_v = 0.0
                end = start + count
                for li in range(start, end):
                    idx = li * 2
                    sum_u += uv_flat[idx]
                    sum_v += uv_flat[idx + 1]

                u = sum_u / count
                v = sum_v / count
                for li in range(start, end):
                    idx = li * 2
                    uv_flat[idx] = u
                    uv_flat[idx + 1] = v

                if ((pi + 1) % step_uv) == 0 or (pi + 1) == total_uv_polys:
                    _log(f"[Voxelator] UV collapse {pi+1}/{total_uv_polys}")

            uv_layer.data.foreach_set("uv", uv_flat)
            mesh.update()
            _log("[Voxelator] UV shrink done")
        else:
            _log("[Voxelator] UV shrink skipped (no active UV layer)")

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
    
