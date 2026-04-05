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
from mathutils import Vector, Matrix
from bpy.props import (
    IntProperty,
    BoolProperty,
    StringProperty
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

def _save_voxel_spritesheet(cubes, target_obj, cell_len, dx, dy, dz, filepath, cube_mat_map=None):
    bb = [target_obj.matrix_world @ Vector(v) for v in target_obj.bound_box]
    min_x = min(v.x for v in bb)
    min_y = min(v.y for v in bb)
    min_z = min(v.z for v in bb)
    ox = min_x + cell_len * 0.5
    oy = min_y + cell_len * 0.5
    oz = min_z + cell_len * 0.5
    layers = [{} for _ in range(dz)]

    cube_count = len(cube_mat_map) if cube_mat_map else len(cubes)
    _log(f"[Voxelator] Building spritesheet from {cube_count} cubes; grid: {dx} {dy} {dz}")
    if cube_mat_map:
        cache = {}
        for (ix, iy, iz), mat in cube_mat_map.items():
            if 0 <= ix < dx and 0 <= iy < dy and 0 <= iz < dz:
                if mat:
                    key = mat.name
                    color = cache.get(key)
                    if color is None:
                        color = _get_color_from_material(mat)
                        cache[key] = color
                    layers[iz][(ix, iy)] = color
    else:
        for o in cubes:
            color = (1.0, 1.0, 1.0, 1.0)
            loc = o.matrix_world.translation
            ix = int(round((loc.x - ox) / cell_len))
            iy = int(round((loc.y - oy) / cell_len))
            iz = int(round((loc.z - oz) / cell_len))
            if 0 <= ix < dx and 0 <= iy < dy and 0 <= iz < dz:
                layers[iz][(ix, iy)] = color
    
    tile = max(dx, dy)
    width = tile * dz
    height = tile
    abs_path = bpy.path.abspath(filepath)
    base = os.path.splitext(os.path.basename(abs_path))[0]
    img = bpy.data.images.new(f"voxel_slices_{base}", width=width, height=height, alpha=True, float_buffer=False)
    px = [0.0] * (width * height * 4)
    off_x = (tile - dx) // 2
    off_y = (tile - dy) // 2
    _log(f"[Voxelator] Spritesheet dimensions: {width} x {height}")
    step_z = max(1, dz // 10)
    for z in range(dz):
        x0 = z * tile
        for (ix, iy), color in layers[z].items():
            px_x = x0 + off_x + ix
            px_y = off_y + iy
            if 0 <= px_x < width and 0 <= px_y < height:
                idx = ((height - 1 - px_y) * width + px_x) * 4
                px[idx] = color[0]
                px[idx + 1] = color[1]
                px[idx + 2] = color[2]
                px[idx + 3] = color[3] if len(color) > 3 else 1.0
        if ((z + 1) % step_z) == 0 or (z + 1) == dz:
            _log(f"[Voxelator] Spritesheet fill {z+1}/{dz}")
    img.pixels = px
    img.filepath_raw = abs_path
    img.file_format = 'PNG'
    img.save()
    _log(f"[Voxelator] Saved spritesheet: {abs_path}")

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
        _log(f"[Voxelator] slices path: {self.slices_filepath or '(default)'}")
        _log(f"[Voxelator] log path: {LOG_FILE}")

        bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked": False, "mode": 'TRANSLATION'})
        context.object.name = source_name + "_voxelized"
        bpy.ops.object.convert(target='MESH')
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        target = context.object
        target_name = target.name
        source.hide_set(True)
        _log(f"[Voxelator] Duplicated & converted: {target_name}")
        _log(f"[Voxelator] Target dims: {target.dimensions[:]}")

        bpy.ops.mesh.primitive_cube_add()
        voxel_cube = context.object
        voxel_cube.name = "voxel_cube"

        cube_size = max(target.dimensions) / (self.voxelizeResolution * 2)
        cell_len = cube_size * 2
        _log(f"[Voxelator] cube_size={cube_size:.6f} cell_len={cell_len:.6f}")

        target.modifiers.new(name='voxel system', type='PARTICLE_SYSTEM')
        target_ps_settings = target.particle_systems[0].settings
        target_ps_settings.count = 1
        target_ps_settings.frame_end = 1
        target_ps_settings.lifetime = 1
        target_ps_settings.emit_from = 'VOLUME' if self.fill_volume else 'FACE'
        target_ps_settings.distribution = 'GRID'
        target_ps_settings.grid_resolution = self.voxelizeResolution
        target_ps_settings.render_type = 'OBJECT'
        target_ps_settings.instance_object = voxel_cube
        target_ps_settings.use_scale_instance = False
        target_ps_settings.show_unborn = True
        target_ps_settings.use_dead = True
        target_ps_settings.particle_size = cube_size
        context.view_layer.update()
        _log(f"[Voxelator][Timing] Setup: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        bb_map = [target.matrix_world @ Vector(v) for v in target.bound_box]
        min_x = min(v.x for v in bb_map)
        min_y = min(v.y for v in bb_map)
        min_z = min(v.z for v in bb_map)
        ox = min_x + cell_len * 0.5
        oy = min_y + cell_len * 0.5
        oz = min_z + cell_len * 0.5

        dx = max(1, int(round(target.dimensions[0] / cell_len)))
        dy = max(1, int(round(target.dimensions[1] / cell_len)))
        dz = max(1, int(round(target.dimensions[2] / cell_len)))

        depsgraph = context.evaluated_depsgraph_get()
        eval_target = target.evaluated_get(depsgraph)
        particles = eval_target.particle_systems[0].particles if eval_target.particle_systems else []

        occupied = set()
        n_particles = len(particles)
        step_particles = max(1, n_particles // 10) if n_particles else 1
        for i, particle in enumerate(particles):
            wloc = eval_target.matrix_world @ particle.location
            ix = int(round((wloc.x - ox) / cell_len))
            iy = int(round((wloc.y - oy) / cell_len))
            iz = int(round((wloc.z - oz) / cell_len))
            if 0 <= ix < dx and 0 <= iy < dy and 0 <= iz < dz:
                occupied.add((ix, iy, iz))
            if ((i + 1) % step_particles) == 0 or (i + 1) == n_particles:
                _log(f"[Voxelator] Particle read {i+1}/{n_particles}")

        _log(f"[Voxelator] Grid: {dx}x{dy}x{dz}")
        _log(f"[Voxelator] Occupied cells: {len(occupied)}")
        _log(f"[Voxelator][Timing] Occupancy: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

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
        _log(f"[Voxelator][Timing] Material map: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        save_path = self.slices_filepath.strip()
        if not save_path:
            save_path = bpy.path.abspath(f"//{source_name}_voxel_slices_{self.voxelizeResolution}.png")
        elif not save_path.lower().endswith(".png"):
            save_path = save_path + ".png"
        _log(f"[Voxelator] Saving spritesheet to: {save_path}")
        _save_voxel_spritesheet([], target, cell_len, dx, dy, dz, save_path, cube_mat_map=cube_mat_map)
        _log(f"[Voxelator][Timing] Spritesheet: {time.perf_counter() - stage_start:.3f}s")
        stage_start = time.perf_counter()

        verts, faces, face_cells = _build_voxel_mesh_data(occupied, ox, oy, oz, cell_len, self.separate_cubes)
        mesh_name = source_name + "_voxel_mesh"
        mesh = bpy.data.meshes.new(mesh_name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(mesh_name, mesh)
        context.collection.objects.link(obj)

        bpy.data.objects.remove(target, do_unlink=True)
        bpy.data.objects.remove(voxel_cube, do_unlink=True)
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
    
