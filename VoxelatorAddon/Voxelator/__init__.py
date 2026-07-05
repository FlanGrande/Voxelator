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

from importlib import reload

from . import voxelator

reload(voxelator)

register = voxelator.register
unregister = voxelator.unregister
