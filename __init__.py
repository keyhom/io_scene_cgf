from bpy.props import (
    BoolProperty,
    FloatProperty,
    StringProperty,
    EnumProperty,
    CollectionProperty,
)
from struct import *
import hashlib
import glob
import re
from bpy_extras.io_utils import (
    ImportHelper,
    orientation_helper,
    path_reference_mode,
    axis_conversion,
    _check_axis_conversion
)
from mathutils import Vector, Matrix
import mathutils
import bpy_extras
import bpy.props
import bpy
import time
import sys
import os

bl_info = {
    "name": "CryTek(AION) CGF format",
    "author": "Jeremy Chen (jeremy7rd@outlook.com)",
    "version": (1, 1, 0),
    "blender": (2, 83, 0),
    "location": "File > Import-Export",
    "description": "Import-Export CGF, Import CGF mesh, UV's, materials and textures",
    "warning": "",
    "support": "TESTING",
    "category": "Import-Export"}

global current_dir


def locate_dependencies():
    # Python dependencies are bundled inside the io_scene_nif/dependencies folder
    global current_dir
    current_dir = os.path.dirname(__file__)
    print(f"current_dir: {current_dir}")
    _dependencies_path = os.path.join(current_dir, "dependencies")
    if _dependencies_path not in sys.path:
        sys.path.append(_dependencies_path)
    del _dependencies_path

    print(f"Loading: Blender io_scene_cgf Addon: 1.1.0")


locate_dependencies()

time.clock = time.perf_counter

@orientation_helper(axis_forward='Y', axis_up='Z')
class AionImporter(bpy.types.Operator, ImportHelper):
    """Load a CtyTek(AION) CGF File"""
    bl_idname = "import_scene.cgf"
    bl_label = "Import CGF/CAF"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = '.cgf'
    filter_glob: StringProperty(
        default="*.cgf;*.caf",
        options={'HIDDEN'},
    )  # type: ignore
    # directory = StringProperty(options={'HIDDEN'})
    #  files = CollectionProperty(name="File Path", type=bpy.types.OperatorFileListElement)

    flip_x_axis: BoolProperty(
        default=False, name="Invert X axis",
        description="Flip the x axis values of the model",
    )  # type: ignore

    import_skeleton: BoolProperty(
        default=True, name="Import Skeleton",
        description="Import the Skeleton bones",
    )  # type: ignore

    skeleton_auto_connect: BoolProperty(
        default=True, name="Auto connect bone",
        description="Auto connect skeleton bones"
    )  # type: ignore

    import_animations: BoolProperty(
        default=False, name="Import Animations",
        description="Import animations by searching the cal"
    )  # type: ignore

    convert_dds_to_png: BoolProperty(
        default=False, name="Convert DDS to PNG",
        description="Convert all the texture images to PNG and save external."
    ) # type: ignore

    reuse_materials: BoolProperty(
        default=False, name="ReUse Materials",
        description="Re-Use the existing materials via name matching."
    ) # type: ignore

    reuse_images: BoolProperty(
        default=False, name="ReUse Images",
        description="Re-Use the existing images via name matching."
    ) # type: ignore

    def execute(self, context: bpy.types.Context):
        #  fnames = [f.name for f in self.files]
        #  if len(fnames) == 0 or not os.path.isfile(os.path.join(self.directory, fnames[0])):
        #  self.report({'ERROR'}, 'No file is selected for import')
        #  return {'FINISHED'}

        keywords = self.as_keywords(ignore=("axis_forward",
                                            "axis_up",
                                            "filter_glob",
                                            "flip_x_axis",
                                            ))

        global_matrix = axis_conversion(
            from_forward=self.axis_forward, from_up=self.axis_up).to_4x4()
        if self.flip_x_axis:
            global_matrix = mathutils.Matrix.Scale(
                -1, 3, (1.0, 0.0, 0.0)).to_4x4() * global_matrix

        keywords['global_matrix'] = global_matrix

        if bpy.data.is_saved and context.user_preferences.filepaths.use_relative_paths:
            keywords['relpath'] = os.path.dirname(bpy.data.filepath)

        self.report({'INFO'}, "Call import_cgf.load(context, **keywords)")

        from .import_cgf import ImportCGF

        importer = ImportCGF()
        return importer.load(context, **keywords)

    def draw(self, context):
        layout = self.layout

        layout.prop(self, "axis_forward")
        layout.prop(self, "axis_up")

        row = layout.row(align=True)
        row.prop(self, "flip_x_axis")

        box = layout.box()
        row = box.row()
        row.prop(self, "import_skeleton", expand=True)

        row = box.row()
        if self.import_skeleton == True:
            row.prop(self, "skeleton_auto_connect")

        row = layout.row(align=True)
        row.prop(self, "import_animations")

        row = layout.row(align=True)
        row.prop(self, "convert_dds_to_png")

        row = layout.row(align=True)
        row.prop(self, "reuse_materials")

        row = layout.row(align=True)
        row.prop(self, "reuse_images")

def menu_func_import(self, context):
    self.layout.operator(AionImporter.bl_idname,
                         text="CryTek(AION) (.cgf, .caf)")


classes = (
    AionImporter,
)


def register():
    script_dir = os.path.abspath(os.path.expanduser(os.path.dirname(__file__)))
    try:
        os.sys.path.index(script_dir)
    except ValueError:
        os.sys.path.append(script_dir)

    global classes, menu_func_import
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

    for cls in classes:
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
