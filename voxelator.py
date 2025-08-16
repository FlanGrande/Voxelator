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
from mathutils import Vector
from bpy.props import (
    IntProperty,
    BoolProperty
)
from bpy.types import (
    AddonPreferences,
    Operator,
    Panel,
    PropertyGroup
)

def _save_voxel_spritesheet(cubes, target_obj, cell_len, dx, dy, dz, filename):
    bb = [target_obj.matrix_world @ Vector(v) for v in target_obj.bound_box]
    min_x = min(v.x for v in bb)
    min_y = min(v.y for v in bb)
    min_z = min(v.z for v in bb)
    ox = min_x + cell_len * 0.5
    oy = min_y + cell_len * 0.5
    oz = min_z + cell_len * 0.5
    layers = [set() for _ in range(dz)]
    for o in cubes:
        loc = o.matrix_world.translation
        ix = int(round((loc.x - ox) / cell_len))
        iy = int(round((loc.y - oy) / cell_len))
        iz = int(round((loc.z - oz) / cell_len))
        if 0 <= ix < dx and 0 <= iy < dy and 0 <= iz < dz:
            layers[iz].add((ix, iy))
    tile = max(dx, dy)
    width = tile * dz
    height = tile
    img = bpy.data.images.new(f"voxel_slices_{filename}", width=width, height=height, alpha=True, float_buffer=False)
    px = [0.0] * (width * height * 4)
    off_x = (tile - dx) // 2
    off_y = (tile - dy) // 2
    for z in range(dz):
        x0 = z * tile
        for ix, iy in layers[z]:
            px_x = x0 + off_x + ix
            px_y = off_y + iy
            if 0 <= px_x < width and 0 <= px_y < height:
                idx = ((height - 1 - px_y) * width + px_x) * 4
                px[idx] = 1.0
                px[idx + 1] = 1.0
                px[idx + 2] = 1.0
                px[idx + 3] = 1.0
    img.pixels = px
    img.filepath_raw = bpy.path.abspath(f"//{filename}.png")
    img.file_format = 'PNG'
    img.save()

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
    
    @classmethod
    def poll(cls, context):
        return context.object.select_get() and context.object.type == 'MESH' or context.object.type == 'CURVE'
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def execute(self, context):

        #set selected object as source
        source_name = bpy.context.object.name
        source = bpy.data.objects[source_name]

        #create copy of object to perform 
        bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked":False, "mode":'TRANSLATION'})
        bpy.context.object.name = source_name + "_voxelized"
        bpy.ops.object.convert(target='MESH')
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

        #hide the original object
        source.hide_set(True)

        #rename the duplicated mesh
        target_name = bpy.context.object.name
        target = bpy.data.objects[target_name]

        #create cube to be used for voxels
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.object.name = "voxel_cube"

        #decide cube size based on resolution and size of original mesh
        cube_size = max(target.dimensions) / (self.voxelizeResolution*2)
        cell_len = cube_size * 2

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

        bpy.context.scene.objects["voxel_cube"].select_set(False)
        bpy.context.scene.objects[target_name].select_set(True)
        existing_names = {o.name for o in bpy.context.scene.objects}

        #create cubes from the particles
        bpy.ops.object.duplicates_make_real()

        dx = max(1, int(round(target.dimensions[0] / cell_len)))
        dy = max(1, int(round(target.dimensions[1] / cell_len)))
        dz = max(1, int(round(target.dimensions[2] / cell_len)))
        new_cubes = [o for o in bpy.context.scene.objects if o.name not in existing_names and o.type == 'MESH']
        _save_voxel_spritesheet(new_cubes, target, cell_len, dx, dy, dz, f"{source_name}_voxel_slices_{self.voxelizeResolution}")

        #remove the duplicated mesh, leaving behind the voxelized mesh
        bpy.data.objects.remove(bpy.data.objects[target_name], do_unlink=True)
        #delete the original cube particle
        bpy.data.objects.remove(bpy.data.objects["voxel_cube"], do_unlink=True)

        #make one of the cubes selected active
        bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]

        #join the cubes into a single mesh
        bpy.ops.object.join()

        bpy.context.object.name = source_name + "_voxel_mesh"
        
        #join cubes together by vertice
        bpy.ops.object.editmode_toggle()
        if self.separate_cubes == False:
            bpy.ops.mesh.remove_doubles()
        bpy.ops.object.editmode_toggle()
        
        #transfer the uv map from the source object to the new cube mesh
        bpy.ops.object.modifier_add(type='DATA_TRANSFER')
        bpy.context.object.modifiers["DataTransfer"].use_loop_data = True
        bpy.context.object.modifiers["DataTransfer"].data_types_loops = {'UV'}
        bpy.context.object.modifiers["DataTransfer"].loop_mapping = 'POLYINTERP_NEAREST'
        bpy.context.object.modifiers["DataTransfer"].object = source
        bpy.ops.object.datalayout_transfer(modifier="DataTransfer")
        bpy.ops.object.modifier_apply(modifier="DataTransfer")

        #make sure each cube is exactly scaled to 1m
        resize_value = 1 / (max(bpy.context.object.dimensions) / self.voxelizeResolution)
        bpy.ops.transform.resize(value=(resize_value, resize_value, resize_value))

        #copy material from source object to cube mesh
        bpy.context.object.active_material = source.active_material

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

        count = 0
        while count < 100:
            bpy.ops.mesh.select_random(ratio=count*.01 + .01, seed=count)
            bpy.ops.uv.select_all(action='SELECT')
            bpy.ops.transform.resize(value=(0.00001, 0.00001, 0.00001))
            bpy.ops.mesh.hide(unselected=False)

            count+=1

        #revert ui areas
        bpy.context.area.ui_type = 'VIEW_3D'
        bpy.ops.mesh.reveal()
        bpy.context.area.ui_type = 'VIEW_3D'

        bpy.ops.object.editmode_toggle()

        #make sure new model is centered
        bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
        bpy.context.object.location[0] = 0
        bpy.context.object.location[1] = 0
        bpy.context.object.location[2] = 0

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
    
