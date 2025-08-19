bl_info = {
    "name": "Voxelator",
    "author": "15shekels aka derpy.radio aka TITANDERP aka Ivan",
    "version": (1, 2, 1),
    "blender": (2, 93, 4),
    "location": "View3D > Object",
    "description": "Converts any mesh into a voxelized mesh made up by cubes",
    "warning": "",
    "wiki_url": "",
    "category": "Object",
}


import bpy
import os
from mathutils import Vector
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

LOG_FILE = bpy.path.abspath("//voxelator.log")
def _log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass

def _get_material_color(mat_slots):
    if not mat_slots:
        return (1.0, 1.0, 1.0, 1.0)
    
    for mat_slot in mat_slots:
        mat = mat_slot.material
        if not mat or not mat.use_nodes:
            continue
        
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

def _save_voxel_spritesheet(cubes, target_obj, cell_len, dx, dy, dz, filepath):
    bb = [target_obj.matrix_world @ Vector(v) for v in target_obj.bound_box]
    min_x = min(v.x for v in bb)
    min_y = min(v.y for v in bb)
    min_z = min(v.z for v in bb)
    ox = min_x + cell_len * 0.5
    oy = min_y + cell_len * 0.5
    oz = min_z + cell_len * 0.5
    layers = [{} for _ in range(dz)]
    _log(f"[Voxelator] Building spritesheet from {len(cubes)} cubes; grid: {dx} {dy} {dz}")
    for o in cubes:
        loc = o.matrix_world.translation
        ix = int(round((loc.x - ox) / cell_len))
        iy = int(round((loc.y - oy) / cell_len))
        iz = int(round((loc.z - oz) / cell_len))
        if 0 <= ix < dx and 0 <= iy < dy and 0 <= iz < dz:
            mat_slots = o.material_slots
            color = _get_material_color(mat_slots) if mat_slots else (1.0, 1.0, 1.0, 1.0)
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
    
    def execute(self, context):

        #set selected object as source
        source_name = bpy.context.object.name
        source = bpy.data.objects[source_name]
        _log(f"[Voxelator] Start: {source_name}")
        _log(f"[Voxelator] res: {self.voxelizeResolution} fill_volume: {self.fill_volume} separate_cubes: {self.separate_cubes}")
        _log(f"[Voxelator] slices path: {self.slices_filepath or '(default)'}")

        #create copy of object to perform 
        bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked":False, "mode":'TRANSLATION'})
        bpy.context.object.name = source_name + "_voxelized"
        bpy.ops.object.convert(target='MESH')
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        _log(f"[Voxelator] Duplicated & converted: {bpy.context.object.name}")

        #hide the original object
        source.hide_set(True)
        _log("[Voxelator] Original hidden")

        #rename the duplicated mesh
        target_name = bpy.context.object.name
        target = bpy.data.objects[target_name]
        _log(f"[Voxelator] Target: {target_name} dims: {target.dimensions[:]}")

        #create cube to be used for voxels
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.object.name = "voxel_cube"
        _log("[Voxelator] Voxel cube added")

        #decide cube size based on resolution and size of original mesh
        cube_size = max(target.dimensions) / (self.voxelizeResolution*2)
        cell_len = cube_size * 2
        _log(f"[Voxelator] cube_size={cube_size:.6f} cell_len={cell_len:.6f}")

        #apply cube particles to duplicated mesh to create voxels
        target.modifiers.new(name='voxel system',type='PARTICLE_SYSTEM')
        target_ps_settings = target.particle_systems[0].settings
        target_ps_settings.count = 1
        target_ps_settings.frame_end = 1
        target_ps_settings.lifetime = 1
        if self.fill_volume == True:
            target_ps_settings.emit_from = 'VOLUME'
        if self.fill_volume == False:
            target_ps_settings.emit_from = 'FACE'
        target_ps_settings.distribution = 'GRID'
        target_ps_settings.grid_resolution = self.voxelizeResolution
        target_ps_settings.render_type = 'OBJECT'
        target_ps_settings.instance_object = bpy.data.objects["voxel_cube"]
        target_ps_settings.use_scale_instance = False
        target_ps_settings.show_unborn = True
        target_ps_settings.use_dead = True
        target_ps_settings.particle_size = cube_size
        _log("[Voxelator] Particle system set")

        bpy.context.scene.objects["voxel_cube"].select_set(False)
        bpy.context.scene.objects[target_name].select_set(True)
        existing_names = {o.name for o in bpy.context.scene.objects}
        _log("[Voxelator] Instantiating particles...")

        #create cubes from the particles
        bpy.ops.object.duplicates_make_real()
        created_count = sum(1 for o in bpy.context.scene.objects if o.name not in existing_names)
        _log(f"[Voxelator] New objects: {created_count}")

        dx = max(1, int(round(target.dimensions[0] / cell_len)))
        dy = max(1, int(round(target.dimensions[1] / cell_len)))
        dz = max(1, int(round(target.dimensions[2] / cell_len)))
        new_cubes = [o for o in bpy.context.scene.objects if o.name not in existing_names and o.type == 'MESH']
        _log(f"[Voxelator] Grid: {dx}x{dy}x{dz}")
        _log(f"[Voxelator] New cubes (mesh): {len(new_cubes)}")
        
        bb_map = [target.matrix_world @ Vector(v) for v in target.bound_box]
        min_x_m = min(v.x for v in bb_map)
        min_y_m = min(v.y for v in bb_map)
        min_z_m = min(v.z for v in bb_map)
        ox_m = min_x_m + cell_len * 0.5
        oy_m = min_y_m + cell_len * 0.5
        oz_m = min_z_m + cell_len * 0.5
        cube_mat_map = {}

        n = len(new_cubes)
        step = max(1, n // 10)
        for i, cube in enumerate(new_cubes):
            cube_loc = cube.matrix_world.translation
            result, location, normal, index = source.closest_point_on_mesh(source.matrix_world.inverted() @ cube_loc)
            if result and index < len(source.data.polygons):
                poly = source.data.polygons[index]
                if poly.material_index < len(source.data.materials):
                    mat = source.data.materials[poly.material_index]
                    if mat:
                        ix = int(round((cube_loc.x - ox_m) / cell_len))
                        iy = int(round((cube_loc.y - oy_m) / cell_len))
                        iz = int(round((cube_loc.z - oz_m) / cell_len))
                        cube_mat_map[(ix, iy, iz)] = mat
            if ((i + 1) % step) == 0 or (i + 1) == n:
                _log(f"[Voxelator] Material transfer {i+1}/{n}")
        
        save_path = self.slices_filepath.strip()
        if not save_path:
            save_path = bpy.path.abspath(f"//{source_name}_voxel_slices_{self.voxelizeResolution}.png")
        elif not save_path.lower().endswith(".png"):
            save_path = save_path + ".png"
        _log(f"[Voxelator] Saving spritesheet to: {save_path}")
        _save_voxel_spritesheet(new_cubes, target, cell_len, dx, dy, dz, save_path)
        _log("[Voxelator] Spritesheet saved")

        #remove the duplicated mesh, leaving behind the voxelized mesh
        bpy.data.objects.remove(bpy.data.objects[target_name], do_unlink=True)
        _log("[Voxelator] Removed temp target")
        #delete the original cube particle
        bpy.data.objects.remove(bpy.data.objects["voxel_cube"], do_unlink=True)
        _log("[Voxelator] Removed voxel_cube")

        #make one of the cubes selected active
        bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]

        #join the cubes into a single mesh
        bpy.ops.object.join()
        _log("[Voxelator] Cubes joined")

        bpy.context.object.name = source_name + "_voxel_mesh"
        _log(f"[Voxelator] New object: {bpy.context.object.name}")
        
        #join cubes together by vertice
        bpy.ops.object.editmode_toggle()
        if self.separate_cubes == False:
            bpy.ops.mesh.remove_doubles()
        bpy.ops.object.editmode_toggle()
        _log("[Voxelator] Merge done" if self.separate_cubes == False else "[Voxelator] Kept cubes separate")
        
        #transfer the uv map from the source object to the new cube mesh
        bpy.ops.object.modifier_add(type='DATA_TRANSFER')
        bpy.context.object.modifiers["DataTransfer"].use_loop_data = True
        bpy.context.object.modifiers["DataTransfer"].data_types_loops = {'UV'}
        bpy.context.object.modifiers["DataTransfer"].loop_mapping = 'POLYINTERP_NEAREST'
        bpy.context.object.modifiers["DataTransfer"].object = source
        bpy.ops.object.datalayout_transfer(modifier="DataTransfer")
        bpy.ops.object.modifier_apply(modifier="DataTransfer")
        _log("[Voxelator] UV transfer applied")

        #make sure each cube is exactly scaled to 1m
        resize_value = 1 / (max(bpy.context.object.dimensions) / self.voxelizeResolution)
        bpy.ops.transform.resize(value=(resize_value, resize_value, resize_value))
        _log("[Voxelator] Resized to 1m cubes")

        bpy.context.object.data.materials.clear()
        #copy all materials from source object to cube mesh
        for mat_slot in source.material_slots:
            if mat_slot.material:
                bpy.context.object.data.materials.append(mat_slot.material)
        _log(f"[Voxelator] Materials appended: {sum(1 for s in source.material_slots if s.material)}")

        obj = bpy.context.object
        mat_name_to_idx = {m.name: i for i, m in enumerate(obj.data.materials)}
        bb_join = [obj.matrix_world @ Vector(v) for v in obj.bound_box]
        min_x_j = min(v.x for v in bb_join)
        min_y_j = min(v.y for v in bb_join)
        min_z_j = min(v.z for v in bb_join)
        cell_post = max(obj.dimensions) / self.voxelizeResolution if self.voxelizeResolution else 1.0
        ox_j = min_x_j + cell_post * 0.5
        oy_j = min_y_j + cell_post * 0.5
        oz_j = min_z_j + cell_post * 0.5

        polys = obj.data.polygons
        total_p = len(polys)
        step_p = max(1, total_p // 10)
        m3 = obj.matrix_world.to_3x3()
        for pi, poly in enumerate(polys):
            wc = obj.matrix_world @ poly.center
            wn = (m3 @ poly.normal).normalized()
            vc = wc - wn * (cell_post * 0.5)
            ix = int(round((vc.x - ox_j) / cell_post))
            iy = int(round((vc.y - oy_j) / cell_post))
            iz = int(round((vc.z - oz_j) / cell_post))
            mat = cube_mat_map.get((ix, iy, iz))
            if mat:
                idx = mat_name_to_idx.get(mat.name, -1)
                if idx != -1:
                    poly.material_index = idx
            if ((pi + 1) % step_p) == 0 or (pi + 1) == total_p:
                _log(f"[Voxelator] Face material assign {pi+1}/{total_p}")

        #shrink uvs so each face is filled with one color
        bpy.ops.object.editmode_toggle()
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.context.area.ui_type = 'UV'
        bpy.context.scene.tool_settings.use_uv_select_sync = False
        if hasattr(bpy.context.space_data.uv_editor, "sticky_select_mode"):
            bpy.context.space_data.uv_editor.sticky_select_mode = 'DISABLED'
        bpy.context.scene.tool_settings.uv_select_mode = 'FACE'
        bpy.context.space_data.pivot_point = 'INDIVIDUAL_ORIGINS'
        bpy.ops.mesh.select_all(action='DESELECT')
        _log("[Voxelator] Shrinking UVs...")

        count = 0
        while count < 100:
            bpy.ops.mesh.select_random(ratio=count*.01 + .01, seed=count)
            bpy.ops.uv.select_all(action='SELECT')
            bpy.ops.transform.resize(value=(0.00001, 0.00001, 0.00001))
            bpy.ops.mesh.hide(unselected=False)

            count+=1
            if count % 20 == 0 or count == 100:
                _log(f"[Voxelator] UV shrink {count}/100")

        #revert ui areas
        bpy.context.area.ui_type = 'VIEW_3D'
        bpy.ops.mesh.reveal()
        bpy.context.area.ui_type = 'VIEW_3D'
        _log("[Voxelator] UV shrink done")

        bpy.ops.object.editmode_toggle()

        #make sure new model is centered
        bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
        bpy.context.object.location[0] = 0
        bpy.context.object.location[1] = 0
        bpy.context.object.location[2] = 0
        _log("[Voxelator] Centered at origin")

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
    
