import array
from gc import collect
import os
import time
import bpy
import bpy_extras
from bpy_extras.io_utils import unpack_list
from bpy_extras.image_utils import load_image
from bpy_extras import node_shader_utils
import mathutils
import math
import itertools
import bmesh

from math import *
from mathutils import *

from bpy_extras.wm_utils.progress_report import ProgressReport, ProgressReportSubstep
from pyffi.formats.cgf import CgfFormat


def to_str(bytes_val) -> str:
    try:
        return bytes_val.decode('utf-8', "replace")
    except:
        return str(bytes_val)


class BoneInfo:
    bone_id = 0
    parent_id = -1
    name = ""
    name_crc_32 = 0
    bind_pos = None
    bind_rot = None
    bind_mat = None
    origin_mat = None
    parent = None
    blender_bone = None
    roll = 0
    head = []
    tail = []
    children = []

    def __init__(self):
        self.bone_id = 0
        self.name = ""
        self.bind_pos = Vector()
        self.bind_rot = Matrix().to_3x3()
        self.bind_mat = Matrix()
        self.origin_mat = Matrix()
        self.parent = None
        self.parent_id = -1
        self.blender_bone = None
        self.roll = 0
        self.head = []
        self.tail = []
        self.children = []


class ImportCGF:

    __slots__ = ['_filepath', 'scale_factor', 'project_root', 'dataname', 'bone_names', 'ob_meshes', 'ob_armature', 'bone_infos',
                 'skin_mesh_chunk', 'animation_map', 'armature_auto_connect', 'animations_loaded', 'dds_convert']

    def __init__(self):
        self.scale_factor = 1.0
        self.dataname = None
        self.bone_names = {}
        self.ob_meshes = []
        self.ob_armature = None
        self.bone_infos = []
        self.skin_mesh_chunk = None
        self._filepath = None
        self.project_root: str = None
        self.animation_map = None
        #  self.armature_auto_connect = False
        self.armature_auto_connect = True
        self.animations_loaded = []
        self.dds_convert = False

    def get_material_name(self, name):
        if isinstance(name, bytes):
            try:
                name = name.decode()
            except Exception as e:
                name = name.decode('euc-kr')
        shader_begin = name.find('(')
        if shader_begin != -1:
            shader_end = name.find(')')
            if shader_end != -1:
                shader_name = name[shader_begin: shader_end]
                if shader_name.upper().startswith('(AION_'):
                    shader_name = '(' + shader_name[6:]

                # shader name can't contains space char.
                shader_name = shader_name.replace(' ', '')
                return name[:shader_begin].lower() + shader_name + name[shader_end:].lower()
        return name

    def is_material_nodraw(self, name):
        if isinstance(name, str):
            name = name.encode()

        s_begin = name.find(b'(')
        if s_begin != -1:
            return name[s_begin+1:s_begin+7].lower() == b'nodraw'
        return False

    def convert_dds_to_png(self, filepath: str):
        if not filepath.lower().endswith('.dds'):
            return False

        _dirname = os.path.dirname(filepath)
        _basename = os.path.basename(filepath)
        _filename, _extname = os.path.splitext(_basename)
        _target_fullpath = os.path.join(_dirname, f'{_filename.lower()}.png')

        # im = bpy_extras.image_utils.load_image(_basename, _dirname, verbose=True, check_existing=True)
        # im.filepath_raw = _target_fullpath
        # im.file_format = 'PNG'
        # im.save()
        ret, = bpy.ops.image.open(filepath=filepath, directory=_dirname, files=[
                                  {'name': _basename}], relative_path=True, show_multiview=True)
        if ret == 'FINISHED':
            im = bpy.data.images[_basename]
            im.save_render(_target_fullpath)
            im.user_clear()
            bpy.data.images.remove(im)

        # TODO: convert dds to png.
        # with Image.open(filepath) as im:
        #     _dirname = os.path.dirname(filepath)
        #     _basename = os.path.basename(filepath)
        #     _filename, _extname = os.path.splitext(_basename)
        #     _target_fullpath = os.path.join(_dirname, f'{_filename.lower()}.png')
        #     im.save(_target_fullpath)

        return True

    def create_std_material(self, chunk: CgfFormat.MtlChunk, reuse_images: bool = False, project_root: str = None):
        """
        Returns blender material from standard material chunk.
        For use with Far Cry.
        """
        assert (isinstance(chunk, CgfFormat.MtlChunk))
        # assert (chunk.type == CgfFormat.MtlType.STANDARD)  # DEBUG
        # TODO: check duplicated imported

        if project_root is None:
            project_root = self.project_root

        cycles_material_wrap_map = {}

        print("Creating material...")
        # get material name
        mtlname = self.get_material_name(chunk.name)
        # print("name: %s\nshader: %s\nscript: %s" % (mtlname, mtlshader, mtlscript))
        print("name: %s" % mtlname)

        # create material
        mat = bpy.data.materials.new(to_str(mtlname))
        # set material parameters

        ma_wrap = node_shader_utils.PrincipledBSDFWrapper(
            mat, is_readonly=False)
        cycles_material_wrap_map[mat] = ma_wrap

        # print(f'chunk.col_d: {chunk.col_d}')
        # print(f'chunk.col_s: {chunk.col_s}')
        # print(f'chunk.col_a: {chunk.col_a}')
        # print(f'chunk: {chunk}')

        diffuse_color = (float(chunk.col_d.r) / 255,
                         float(chunk.col_d.g) / 255,
                         float(chunk.col_d.b) / 255)
        specular_color = (float(chunk.col_s.r) / 255,
                          float(chunk.col_s.g) / 255,
                          float(chunk.col_s.b) / 255)
        ambient_color = (float(chunk.col_a.r) / 255,
                         float(chunk.col_a.g) / 255,
                         float(chunk.col_a.b) / 255)

        ma_wrap.emission_strength = chunk.self_illum

        ma_wrap.base_color = diffuse_color
        ma_wrap.specular = sum(specular_color) / 3
        ma_wrap.specular_tint = chunk.spec_level
        ma_wrap.roughness = int((1.0 - chunk.spec_shininess) * 8.0)
        # ma_wrap.metallic = sum(ambient_color) / 3

        # Don't load the same image multiple times
        context_imagepath_map = {}

        project_root = '' if (project_root is None) else project_root

        def determine_texture_map(chunk: CgfFormat.MtlChunk, name: str) -> bool:
            tex_map = chunk.__getattribute__(name)
            if tex_map and tex_map.type > 0:
                print(
                    f"{chunk.name} -> texture ({name}): long_name = {tex_map.long_name}, type = {tex_map.type}")
                return True
            return False

        def load_material_image(image_path, alias_name=None, reuse_images: bool = False):
            print("load_material_image: %s" % image_path)
            filepath = image_path
            if not os.path.isabs(image_path):
                filepath = os.path.join(project_root, image_path)
            if not os.path.exists(filepath):
                filepath = os.path.join(
                    project_root, os.path.basename(image_path))

            filepath = filepath.replace('\\', '/').replace('//', '/')

            base_name = os.path.basename(filepath)
            dir_name = os.path.dirname(filepath)

            fileNameWithoutExt, fileNameExt = os.path.splitext(base_name)

            if self.dds_convert and fileNameExt.lower() == ".dds":
                self.convert_dds_to_png(filepath)
                base_name = fileNameWithoutExt.lower() + '.png'

            image = None
            if reuse_images:
                if bpy.data.images.find(base_name) != -1:
                    image = bpy.data.images.get(base_name)

            if image is None:
                image = load_image(base_name, dir_name)
            if not alias_name:
                alias_name = os.path.basename(image_path)

            return (alias_name, image)

        alpha_test = chunk.alpha_test > 0.0 and chunk.alpha_test < 1.0

        # determines how many textures specified.
        if chunk.type == 1:
            has_opacity_texture = False
            if determine_texture_map(chunk, 'tex_o'):
                (alias_name, image) = load_material_image(to_str(chunk.tex_o.long_name), to_str(
                    chunk.tex_o.name) if chunk.tex_o.name else None, reuse_images)

                # opacity_texture = node_shader_utils.ShaderImageTextureWrapper(ma_wrap, ma_wrap.node_principled_bsdf, ma_wrap.node_principled_bsdf.inputs['Alpha'])
                # opacity_texture.image = image
                # opacity_texture.texcoords = 'UV'
                ma_wrap.alpha_texture.image = image
                ma_wrap.alpha_texture.texcoords = 'UV'
                ma_wrap.material.node_tree.links.new(
                    ma_wrap.node_principled_bsdf.inputs['Alpha'], ma_wrap.alpha_texture.node_image.outputs['Alpha'])
                has_opacity_texture = True

            if determine_texture_map(chunk, 'tex_d'):
                (alias_name, image) = load_material_image(to_str(chunk.tex_d.long_name),
                                                          to_str(chunk.tex_d.name) if chunk.tex_d.name else None, reuse_images)
                ma_wrap.base_color_texture.image = image
                ma_wrap.base_color_texture.texcoords = 'UV'

                if not has_opacity_texture and (chunk.opacity < 1.0 or alpha_test):
                    ma_wrap.material.node_tree.links.new(
                        ma_wrap.node_principled_bsdf.inputs['Alpha'], ma_wrap.base_color_texture.node_image.outputs['Alpha'])

            if determine_texture_map(chunk, 'tex_a'):
                (alias_name, image) = load_material_image(to_str(chunk.tex_a.long_name), to_str(
                    chunk.tex_a.name) if chunk.tex_a.name else None, reuse_images)
                ma_wrap.emission_color_texture.image = image
                ma_wrap.emission_color_texture.texcoords = 'UV'
            if determine_texture_map(chunk, 'tex_s'):
                (alias_name, image) = load_material_image(to_str(chunk.tex_s.long_name), to_str(
                    chunk.tex_s.name) if chunk.tex_s.name else None, reuse_images)
                ma_wrap.specular_texture.image = image
                ma_wrap.specular_texture.texcoords = 'UV'

            if determine_texture_map(chunk, 'tex_b'):
                (alias_name, image) = load_material_image(to_str(chunk.tex_b.long_name), to_str(
                    chunk.tex_b.name) if chunk.tex_b.name else None, reuse_images)
                ma_wrap.normalmap_texture.image = image
                ma_wrap.normalmap_texture.texcoords = 'UV'
            if determine_texture_map(chunk, 'tex_g'):
                (alias_name, image) = load_material_image(to_str(chunk.tex_g.long_name), to_str(
                    chunk.tex_g.name) if chunk.tex_g.name else None, reuse_images)
                ma_wrap.roughness_texture.image = image
                ma_wrap.roughness_texture.texcoords = 'UV'
            if determine_texture_map(chunk, 'tex_f'):
                print('No implemented for tex_f.');
                pass
            if determine_texture_map(chunk, 'tex_c'):
                print('No implemented for tex_f.');
                pass
            if determine_texture_map(chunk, 'tex_r'):
                (alias_name, image) = load_material_image(to_str(chunk.tex_r.long_name), to_str(
                    chunk.tex_r.name) if chunk.tex_r.name else None, reuse_images)
                ma_wrap.metallic_texture.image = image
                ma_wrap.metallic_texture.texcoords = 'UV'
            if determine_texture_map(chunk, 'tex_subsurf'):
                print('No implemented for tex_subsurf.');
                pass
            if determine_texture_map(chunk, 'tex_detail'):
                print('No implemented for tex_detail.');
                pass

        if chunk.opacity < 1.0:
            ma_wrap.alpha = chunk.opacity
            mat.blend_method = 'BLEND'
            mat.shadow_method = 'HASHED'
        elif alpha_test:
            mat.blend_method = 'CLIP'
            mat.shadow_method = 'CLIP'
            mat.alpha_threshold = chunk.alpha_test

        if chunk.flags.two_sided == 1:
            mat.use_backface_culling = False
        else:
            mat.use_backface_culling = True

        ma_wrap.update()

        del load_material_image

        return mat

    def create_mesh(self, new_objects,
                    mesh_chunk: CgfFormat.MeshChunk,
                    unique_materials: list[bpy.types.Material],
                    dataname: str):
        assert (isinstance(mesh_chunk, CgfFormat.MeshChunk))

        verts_loc = []
        verts_nor = []
        verts_tex = None
        verts_col = [] if (mesh_chunk.has_vertex_colors) else None
        faces = []
        uv_faces = None

        me = bpy.data.meshes.new(dataname)

        for i, (vert, norm) in enumerate(zip(mesh_chunk.get_vertices(), mesh_chunk.get_normals())):
            verts_loc.append((vert.x, vert.y, vert.z))
            verts_nor.append((norm.x, norm.y, norm.z))

        for i, (f) in enumerate(mesh_chunk.get_triangles()):
            faces.append(f)

        verts_tex = list(mesh_chunk.get_uvs())

        if mesh_chunk.has_vertex_colors:
            verts_col = list(mesh_chunk.get_colors())

        uv_faces = list(mesh_chunk.get_uv_triangles())

        if len(faces) != len(uv_faces) and len(uv_faces) == 0:
            uv_faces = len(faces) * [(0, 0)]

        me.from_pydata(verts_loc, [], faces)

        print("Mesh num vertices: %i" % len(me.vertices))
        print("Mesh num polygon: %i" % len(me.polygons))
        print("Mesh num loops: %i" % len(me.loops))

        #  for material in unique_materials:
        #  me.materials.append(material)

        use_mat_ids = []

        if verts_nor and me.loops:
            me.create_normals_split()
            # or me.split_faces()

        if verts_tex and me.polygons:
            me.uv_layers.new()
            # me.uv_textures.new()

        if verts_col and len(verts_col):
            me.vertex_colors.new()

        context_material_old = -1  # avoid a dict lookup
        mat = 0  # rare case it may be un-initialized
        material_index = 0

        def get_smooth_group_indices():
            if mesh_chunk.faces:
                for face in mesh_chunk.faces:
                    yield face.sm_group
            elif mesh_chunk.mesh_subsets:
                for meshsubset in mesh_chunk.mesh_subsets.mesh_subsets:
                    for i in range(meshsubset.num_indices // 3):
                        yield meshsubset.sm_group

        for i, (face, uv_face, blen_poly, context_material_id, context_smooth_group) in enumerate(zip(faces, uv_faces, me.polygons,
                                                                                                      mesh_chunk.get_material_indices(), get_smooth_group_indices())):
            if context_smooth_group > 0:
                blen_poly.use_smooth = True

            if context_material_id >= 0:
                if context_material_old != context_material_id:
                    mat = context_material_id
                    context_material_old = context_material_id
                    try:
                        idx = use_mat_ids.index(mat)
                        material_index = idx
                    except ValueError:
                        use_mat_ids.append(mat)
                        material_index = len(use_mat_ids) - 1

                blen_poly.material_index = material_index
            else:
                print(
                    f'mesh_chunk.get_material_indices() return a material id less than 0.')

            blen_uvs = None
            if len(me.uv_layers) > 0:
                blen_uvs = me.uv_layers[0]

            blen_vcs = me.vertex_colors[0] if (
                verts_col and len(verts_col)) else None

            if verts_nor:
                for face_idx, face_uvidx, lidx in zip(face, uv_face, blen_poly.loop_indices):
                    me.loops[lidx].normal[:] = verts_nor[0 if (
                        face_idx is ...) else face_idx]
                    if blen_uvs is not None:
                        blen_uvs.data[lidx].uv = verts_tex[0 if (
                            face_uvidx is ...) else face_uvidx]
                    if blen_vcs:
                        (c1, c2, c3, c4) = verts_col[0 if (
                            face_idx is ...) else face_idx]
                        blen_vcs.data[lidx].color = (c1, c2, c3, c4)

            if verts_tex and uv_face:
                if context_material_id:
                    # TODO: set texture image
                    pass

        print('Use material ids: %i' % len(use_mat_ids))

        bNoDraw = True

        if len(use_mat_ids):
            for mat_id in use_mat_ids:
                print('Use material is(%i) => %s' %
                      (mat_id, unique_materials[mat_id]))
                me.materials.append(unique_materials[mat_id][0])
                bNoDraw = bNoDraw and unique_materials[mat_id][1]

        me.validate(clean_customdata=False)
        me.update(calc_edges=False)

        if verts_nor:
            clnors = array.array('f', [0.0] * (len(me.loops) * 3))
            me.loops.foreach_get("normal", clnors)

            me.normals_split_custom_set(tuple(zip(*(iter(clnors),) * 3)))
            # me.use_auto_smooth = True
            # me.show_edge_sharp = True

        ob = bpy.data.objects.new(me.name, me)
        new_objects[mesh_chunk] = ob

        print('Hide Preview and Render: %i' % bNoDraw)
        if bNoDraw:
            # ob.hide_viewport = True
            # ob.hide_set(True)
            ob.hide_render = True

    def parse_bone_name_list(self, chunk):
        assert (isinstance(chunk, CgfFormat.BoneNameListChunk))
        print("Num of bones: %d" % chunk.num_names)
        self.bone_infos = [None] * chunk.num_names
        from zlib import crc32
        for i, name in enumerate(chunk.names):
            k = crc32(name.encode('ascii'))
            name = name.replace(' ', '_')
            self.bone_names[k] = name
            info = BoneInfo()
            info.name = name
            info.name_crc_32 = k
            self.bone_infos[i] = info

    def build_bone_infos(self, chunk):
        assert (isinstance(chunk, CgfFormat.BoneAnimChunk))
        bone_entries = list(chunk.bones)

        for i, bone_entry in enumerate(bone_entries):
            info = self.bone_infos[i]
            info.bone_id = bone_entry.bone_id
            info.parent_id = bone_entry.parent_id
            if info.parent_id != -1:
                info.parent = self.bone_infos[info.parent_id]
                info.parent.children.append(info)

    def create_armatures(self, chunk, new_objects, scale_factor=1.0):
        assert (isinstance(chunk, CgfFormat.BoneAnimChunk))
        #  dataname = self.dataname + "Skeleton"
        dataname = "Skeleton"
        anim = bpy.data.armatures.new(dataname)

        anim_obj = bpy.data.objects.new(dataname, anim)
        collection = bpy.context.view_layer.active_layer_collection.collection
        collection.objects.link(anim_obj)
        for i in collection.objects:
            i.select_set(False)  # deselect all objects
        anim_obj.select_set(True)

        new_objects[chunk] = anim_obj

        # anim_obj.show_x_ray = True

        # set current armature to edit the bone
        bpy.context.view_layer.objects.active = anim_obj

        # set mode to able to edit the bone
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='EDIT')

        bpy.ops.object.mode_set(mode='OBJECT')

        def vec_roll_to_mat3(vec, roll):
            nor = vec.normalized()

            THETA_THRESHOLD_NEGY = 1.0e-09
            THETA_THRESHOLD_NEGY_CLOSE = 1.0e-05

            # create a 3x3 matrix
            bMatrix = Matrix().to_3x3()

            theta = 1.0 + nor[1]

            if (theta > THETA_THRESHOLD_NEGY_CLOSE) or ((nor[0] or nor[2]) and theta > THETA_THRESHOLD_NEGY):
                bMatrix[1][0] = -nor[0]
                bMatrix[0][1] = nor[0]
                bMatrix[1][1] = nor[1]
                bMatrix[2][1] = nor[2]
                bMatrix[1][2] = -nor[2]
                if theta > THETA_THRESHOLD_NEGY_CLOSE:
                    # If nor is far enough from -Y, apply the general case.
                    bMatrix[0][0] = 1 - nor[0] * nor[0] / theta
                    bMatrix[2][2] = 1 - nor[2] * nor[2] / theta
                    bMatrix[0][2] = bMatrix[2][0] = -nor[0] * nor[2] / theta
                else:
                    # If nor is too close to -Y, apply the special case
                    theta = nor[0] * nor[0] + nor[2] * nor[2]
                    bMatrix[0][0] = (nor[0] + nor[2]) * \
                        (nor[0] - nor[2]) / -theta
                    bMatrix[2][2] = -bMatrix[0][0]
                    bMatrix[0][2] = bMatrix[2][0] = 2.0 * \
                        nor[0] * nor[2] / theta
            else:
                # If nor is -Y, simple symmetry by Z axis
                bMatrix = Matrix().to_3x3()
                bMatrix[0][0] = bMatrix[1][1] = -1.0

            # Make roll matrix
            rMatrix = Matrix.Rotation(roll, 3, nor)
            mat = rMatrix @ bMatrix
            return mat

        def mat3_to_vec_roll(mat):
            vec = mat.col[1]
            vecmat = vec_roll_to_mat3(mat.col[1], 0)
            vecmatinv = vecmat.inverted()
            rollmat = vecmatinv @ mat
            roll = math.atan2(rollmat[0][2], rollmat[2][2])
            return vec, roll

        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='EDIT')

        for info in self.bone_infos:
            # Go to edit mode for the bones
            bpy.ops.object.mode_set(mode='EDIT')

            #  bpy.ops.armature.bone_primitive_add(name=info.name)
            newbone = anim_obj.data.edit_bones.get(info.name)
            if newbone is None:
                newbone = anim_obj.data.edit_bones.new(info.name)
            info.blender_bone = newbone
            parent_bone = info.parent.blender_bone if info.parent else None
            newbone.parent = parent_bone

            axis, roll = mat3_to_vec_roll(info.bind_mat.to_3x3())

            axis.z = axis.z + 0.001

            newbone.head = info.bind_pos
            tmp_tail = info.bind_pos + axis / 5

            if len(info.children) == 0:
                newbone.tail = tmp_tail
            elif len(info.children) == 1:
                direction = info.bind_mat.inverted() @ Vector(info.tail)
                nor = direction.normalized()
                error_limit = 0.0001
                if nor.dot(nor) > error_limit:
                    newbone.tail = info.tail
                else:
                    newbone.tail = info.bind_mat @ Vector((0, 0.05, 0))
            else:
                newbone.tail = info.tail

            #  newbone.use_inherit_rotation = False
            newbone.use_local_location = False

            newbone.matrix = info.bind_mat

            epsilon = 1.19209290E-07

            if info.parent:
                distance = parent_bone.tail - newbone.head
                #  # Auto connect the bone that head locate at the parent's tail.
                if self.armature_auto_connect and distance.dot(distance) <= epsilon:
                    #  and distance.x < epsilon and distance.y < epsilon and distance.z < epsilon:
                    newbone.use_connect = True

        # bpy.context.scene.update()

        self.mapping_vertex_group_weights(new_objects)

        mesh_obj = new_objects.get(self.skin_mesh_chunk)

        if mesh_obj:
            if mesh_obj not in collection.objects.values():
                collection.objects.link(mesh_obj)

        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

        bpy.ops.object.select_all(action='DESELECT')  # deselect all object

        mesh_obj.select_set(True)
        anim_obj.select_set(True)

        bpy.context.view_layer.objects.active = anim_obj
        bpy.ops.object.parent_set(type='ARMATURE')

    def mapping_vertex_group_weights(self, new_objects):
        if not self.bone_infos:
            return

        # Vertex Group/Weight
        mesh_obj = new_objects.get(self.skin_mesh_chunk)
        if mesh_obj:
            for info in self.bone_infos:
                if not mesh_obj.vertex_groups.get(info.name):
                    mesh_obj.vertex_groups.new(name=info.name)

        if self.skin_mesh_chunk and self.skin_mesh_chunk.has_vertex_weights:
            # import vertex weight from cgf mesh data.
            for i, vw in enumerate(self.skin_mesh_chunk.vertex_weights):
                for bl in vw.bone_links:
                    rel_group_name = self.bone_infos[bl.bone].name
                    blending = bl.blending
                    #  mesh_obj.vertex_groups[rel_group_name].add([i], blending, 'ADD')
                    mesh_obj.vertex_groups[rel_group_name].add(
                        [i], blending, 'REPLACE')

    def get_bone_head_pos(self, bone_info):
        pos_head = [0.0] * 3
        pos = bone_info.bind_mat.to_translation()

        pos_head[0] = pos.x
        pos_head[1] = pos.y
        pos_head[2] = pos.z

        return pos_head

    def get_bone_tail_pos(self, bone_info):
        pos_tail = [0.0] * 3
        ischildfound = False
        children = []

        for info in self.bone_infos:
            #  print('Parent: %s | Bone Info: %s' % (info.parent, bone_info))
            if info.parent and info.parent == bone_info:
                ischildfound = True
                children.append(info)

        if ischildfound:
            tmp_head = [0.0] * 3
            for info in children:
                tmp_head[0] += info.head[0]
                tmp_head[1] += info.head[1]
                tmp_head[2] += info.head[2]
            tmp_head[0] /= len(children)
            tmp_head[1] /= len(children)
            tmp_head[2] /= len(children)
            if bone_info.parent is None:  # Specify root bone, move a little bit preventing invalid data to be removed
                tmp_head[2] += CgfFormat.EPSILON
            #  print('Return tmp_head %s for bone: %s' % (tmp_head, bone_info.name))
            return tmp_head
        else:
            tmp_len = 0.0
            parent_head = [0.0] * 3
            if bone_info.parent:
                parent_head = bone_info.parent.head
            tmp_len += (bone_info.head[0] - parent_head[0]) ** 2
            tmp_len += (bone_info.head[1] - parent_head[1]) ** 2
            tmp_len += (bone_info.head[2] - parent_head[2]) ** 2
            tmp_len = tmp_len ** 0.5 * 0.5
            pos_tail[0] = bone_info.head[0] + \
                tmp_len * bone_info.bind_mat[0][0]
            pos_tail[1] = bone_info.head[1] + \
                tmp_len * bone_info.bind_mat[1][0]
            pos_tail[2] = bone_info.head[2] + \
                tmp_len * bone_info.bind_mat[2][0]
            #  print("Return pos_tail %s for bone: %s" % (pos_tail, bone_info.name))
            return pos_tail

    def process_bone_initial_position(self, chunk):
        assert (isinstance(chunk, CgfFormat.BoneInitialPosChunk))

        self.skin_mesh_chunk = chunk.mesh

        fix_z = Quaternion((0, 0, 1), math.radians(90)).to_matrix()
        # fix_z = Matrix.Rotation(math.radians(-90), 4, 'Z')

        for i, mat in enumerate(chunk.initial_pos_matrices):
            info = self.bone_infos[i]
            info.bind_pos = Vector((mat.pos.x, mat.pos.y, mat.pos.z))
            info.bind_rot = Matrix(mat.rot.as_tuple()).transposed()

            cgf_mat = CgfFormat.Matrix44()
            cgf_mat.set_identity()
            cgf_mat.set_matrix_33(mat.rot)
            cgf_mat.set_translation(mat.pos)

            #  print('### %s' % info.name)
            #  print('---')
            #  print(cgf_mat)
            #  print(Matrix(cgf_mat.as_tuple()).transposed())
            #  print('\n')

            info.bind_mat = Matrix(cgf_mat.as_tuple()).transposed()
            info.bind_mat = info.bind_mat @ fix_z.transposed().to_4x4()
            info.origin_mat = info.bind_mat.copy()

        for info in self.bone_infos:
            info.head = self.get_bone_head_pos(info)

        for info in self.bone_infos:
            info.tail = self.get_bone_tail_pos(info)

    def get_animation_list(self):
        if self.animation_map:
            return self.animation_map.keys()

        def resolve_relative_path(base_path, target_path):
            if not os.path.isdir(base_path):
                base_path = os.path.dirname(base_path)
            (target_path, _) = os.path.splitext(target_path)
            target_path = os.path.dirname(target_path)
            if base_path.lower()[-len(target_path):] == target_path.lower():
                return base_path[:-len(target_path)]
            elif self.project_root:
                return os.path.join(self.project_root, 'Objects')
            else:
                return ''

        (dirpath, filename) = os.path.split(self.filepath)
        (_, filename_ext) = os.path.splitext(self.filepath)
        if filename_ext == '.cgf':
            filename = filename[:-4] + '.cal'
            filepath = os.path.join(dirpath, filename)
            if os.path.exists(filepath):
                with open(os.path.join(dirpath, filename), 'rb') as cal:
                    lines = cal.readlines()
                    for line in lines:
                        line = to_str(line).lstrip()
                        if len(line) == 0 or line.startswith('//'):  # ignore comments
                            continue
                        # remove comments at line end.
                        arr = line.split('//', maxsplit=2)
                        line = arr[0]
                        # print(line, end="")
                        arr = line.split('=')
                        action_name = arr[0].strip()
                        if action_name == 'everytime':
                            continue
                        animation_filepath = arr[1].strip()
                        animation_filepath = animation_filepath.replace(
                            '\\', os.path.sep)
                        animation_file = os.path.join(resolve_relative_path(
                            self.filepath, animation_filepath), animation_filepath)
                        animation_file = os.path.abspath(animation_file)
                        # print(action_name,'=', animation_file)
                        if self.animation_map is None:
                            self.animation_map = {}
                        if not self.animation_map.get(action_name):
                            self.animation_map[action_name] = {}
                        self.animation_map[action_name]['filepath'] = animation_file
            return self.animation_map.keys() if self.animation_map else None
        else:
            return None

    def get_animation_info(self, action_name):
        action_name_list = self.get_animation_list()
        if action_name_list is None:
            return None
        if action_name not in action_name_list:
            return None
        return self.animation_map.get(action_name)

    def parse_animation_controller(self, chunk, anim_info, scale=1.0):
        assert (isinstance(chunk, CgfFormat.ControllerChunk))
        try:
            bone_name = self.bone_names[chunk.ctrl_id]
        except KeyError:
            bone_name = None
        print('Parsing Animation Controller for Bone: \"%s\" (%i) ...' %
              (bone_name, chunk.ctrl_id))
        assert (chunk.type == CgfFormat.CtrlType.NONE)

        if bone_name is None:
            return

        ctrls = anim_info.get('ctrls')
        if ctrls is None:
            ctrls = []
            anim_info['ctrls'] = ctrls

        keyframes = []

        for idx, k in enumerate(chunk.keys):
            mat = Matrix()
            pos = Vector((k.abs_pos.x, k.abs_pos.y, k.abs_pos.z)) * scale
            rot = Quaternion((k.rel_quat.w, k.rel_quat.x,
                             k.rel_quat.y, k.rel_quat.z))
            mat = Matrix.Translation(pos) @ rot.to_matrix().to_4x4().inverted()
            keyframe = (k.time, pos, rot, mat)
            keyframes.append(keyframe)

        ctrls.append((bone_name, chunk.ctrl_id, keyframes))

    def load_animations(self):
        action_name_list = self.get_animation_list()
        if action_name_list and len(action_name_list):
            print('Loading animations in a cycle loop ...')
            for action_name in action_name_list:
                self.load_animation(action_name)

            print('\nAll animation loaded.')
        else:
            print('No action list found.')

    def load_animation(self, action_name=None):
        only_caf = self.filepath.endswith('.caf')
        if not only_caf:
            if action_name is None or len(action_name) == 0:
                raise ValueError('Invalid action_name')
            anim_info = self.get_animation_info(action_name)
            if anim_info is None:
                return None
        else:
            anim_info = {'filepath': self.filepath}

        filepath = anim_info['filepath']

        blen_action_name = os.path.basename(os.path.splitext(filepath)[0])

        if blen_action_name in self.animations_loaded:  # Preventing duplicate loading
            return None

        print("Ready to load animation %s with action %s as %s" %
              (filepath, action_name, blen_action_name))
        with open(filepath, 'rb') as caf:
            data = CgfFormat.Data()
            try:
                data.inspect_version_only(caf)
            except ValueError as e:
                print(e)

            try:
                data.read(caf)
            except:
                raise

        for chunk in data.chunks:
            if len(self.bone_names.keys()) == 0 and isinstance(chunk, CgfFormat.BoneNameListChunk):
                self.parse_bone_name_list(chunk)

        scale_factor = 1.0 / self.get_global_scale(data)

        for i, chunk in enumerate(data.chunks):
            if isinstance(chunk, CgfFormat.AnimChunk):
                anim_info['num_keys'] = chunk.key_nums
                anim_info['position'] = Vector((chunk.initial_pos.x, chunk.initial_pos.y,
                                                chunk.initial_pos.z))
            elif isinstance(chunk, CgfFormat.TimingChunk):
                anim_info['secs_per_tick'] = 1.0 * chunk.secs_per_tick
                anim_info['ticks_per_frame'] = 1.0 * chunk.ticks_per_frame
                anim_info['start_frame'] = chunk.global_range.start
                anim_info['end_frame'] = chunk.global_range.end
            elif isinstance(chunk, CgfFormat.ControllerChunk):
                self.parse_animation_controller(
                    chunk, anim_info, scale=scale_factor)

        bpy.context.scene.frame_start = anim_info['start_frame']
        bpy.context.scene.frame_end = anim_info['end_frame']
        bpy.context.scene.render.fps_base = 1.0
        fps = 1.0 / (anim_info['secs_per_tick'] * anim_info['ticks_per_frame'])
        bpy.context.scene.render.fps = math.ceil(fps)

        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

        active_obj = bpy.context.view_layer.objects.active
        if active_obj.type == 'ARMATURE':
            pass
        elif active_obj.type == 'MESH' and active_obj.parent is not None and active_obj.parent.type == 'ARMATURE':
            active_obj = active_obj.parent
        assert active_obj.type == 'ARMATURE', "The active obj is not an Armature or child of Armature"
        armature_name = active_obj.name
        armature_data_name = active_obj.data.name
        print("Armature: %s" % armature_name)
        print("Armature Data: %s" % armature_data_name)

        # bpy.context.scene.update()

        # Creates an new animation data if the armature obj was no animation data attached.
        obj = bpy.data.objects[armature_name]
        if obj.animation_data is None:
            obj.animation_data_create()
        # New an action and set to the current animation action.
        action = bpy.data.actions.new(name=blen_action_name)
        obj.animation_data.action = action

        # action.frame_start = anim_info['start_frame']
        # action.frame_end = anim_info['end_frame']

        # Parsing the source controllers into blender action data.
        ctrls = anim_info['ctrls']

        fix_z = Matrix.Rotation(math.radians(-90), 4, 'Z')

        for ctrl in ctrls:
            (bone_name, bone_key, keyframes) = ctrl
            pose_bones = obj.pose.bones

            # Ignores the bone that doesn't exists in the pose
            if bone_name not in pose_bones:
                continue

            for keyframe in keyframes:
                (time, pos, rot, mat) = keyframe
                raw_key_index = int(time / anim_info['ticks_per_frame'])
                bpy.context.scene.frame_set(raw_key_index)

                if pose_bones[bone_name].parent is not None:
                    trans = pose_bones[bone_name].parent.matrix @ fix_z.transposed() @ mat @ fix_z
                else:
                    trans = mat @ fix_z

                pose_bones[bone_name].matrix = trans

                pose_bones[bone_name].keyframe_insert('rotation_quaternion')
                pose_bones[bone_name].keyframe_insert('location')

        self.animations_loaded.append(blen_action_name)

        bpy.context.scene.frame_set(0)
        # bpy.context.scene.update()

    def inspect_project_root(self, top_level_dir='Objects'):
        if self._filepath is None:
            return None
        obj_path_idx = self._filepath.find(
            os.path.sep + top_level_dir + os.path.sep)
        if obj_path_idx != -1:
            return os.path.abspath(self._filepath[:obj_path_idx])
        return None

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, value):
        """
        store the filepath at instance scope, so also determine the project root directory.
        """
        self._filepath = value

        for rd in ['Objects', 'Levels', 'Effects']:
            project_root = self.inspect_project_root(rd)
            if project_root is not None:
                break

        if project_root is None:
            project_root = os.path.dirname(os.path.abspath(value))

        self.project_root = project_root

    def get_global_scale(self, cgf_data):
        scale = self.scale_factor
        if cgf_data.game == 'Crysis':
            scale /= 100.0
        elif cgf_data.game == 'Aion':
            scale *= 100.0
        return scale

    def load(self, context: bpy.types.Context,
             filepath: str,
             *,
             convert_dds_to_png=False,
             reuse_materials=False,
             reuse_images=False,
             import_skeleton=True,
             skeleton_auto_connect=True,
             import_animations=False,
             scale_factor=1.0,
             relpath=None,
             global_matrix: Matrix = None
             ):
        """
        Called by the use interface or another script.
        load_cgf(path) - should give acceptable result.
        This function passes the file and sends the data off
            to be split into objects and then converted into mesh objects
        """

        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

        self.filepath = filepath
        self.armature_auto_connect = skeleton_auto_connect
        self.scale_factor = scale_factor
        self.dds_convert = convert_dds_to_png

        if self.filepath.endswith('.caf'):
            self.load_animation()
            return {'FINISHED'}

        with ProgressReport(context.window_manager) as progress:
            progress.enter_substeps(
                1, "Importing CGF %r ... relpath: %r" % (filepath, relpath))

            if global_matrix is None:
                global_matrix = Matrix()

            time_main = time.time()
            b_mats = []

            progress.enter_substeps(1, "Parsing CGF file ...")
            with open(filepath, 'rb') as f:
                data = CgfFormat.Data()
                # check if cgf file is valid
                try:
                    data.inspect_version_only(f)
                except ValueError:
                    # not a cgf file
                    raise
                else:
                    progress.enter_substeps(2, "Reading CGF %r ..." % filepath)
                    data.read(f)

            print('Project root: %s' % self.project_root)

            progress.leave_substeps("Done reading.")
            progress.enter_substeps(3, "Parsing CGF %r ..." % filepath)

            if data.game == 'Crysis':
                print(
                    '[WARNING]: Crysis import is very experimental, and is likely to fail')

            print('game:                    %s' % data.game)
            print('file type:               0x%08X' % data.header.type)
            print('version:                 0x%08X' % data.version)
            print('user version:            0x%08X' % data.user_version)

            for i, chunk in enumerate(data.chunks):
                print('id %i: %s' % (i, chunk.__class__.__name__))

            # TODO: fixed the scale correction
            scale_factor = self.get_global_scale(data)

            for chunk in data.chunks:
                chunk.apply_scale(1.0 / scale_factor)

            # import data
            progress.step("Done, making data into blender")

            progress.step("Done, loading materials and images ...")
            # TODO: create materials

            # Far Cry: iterate over all standard material chunks
            for chunk in data.chunks:
                # check chunk type
                if not isinstance(chunk, CgfFormat.MtlChunk):
                    continue

                # multi material: skip
                # if chunk.children or to_str(chunk.name).startswith('s_nouvmap') \
                #         or chunk.type != CgfFormat.MtlType.STANDARD \
                #         or self.get_material_name(chunk.name) is None:
                #     print(f'Ignore MtlChunk: {chunk.name}')
                #     continue
                if chunk.type == CgfFormat.MtlType.MULTI:
                    print(
                        f'Ignore MtlChunk: {chunk.name}, because of MtlType.MULTI')
                    continue
                elif self.get_material_name(chunk.name) is None:
                    print(
                        f'Ignore MtlChunk: {chunk.name}, unlegal material name.')
                    b_mats.append((None, True))
                    continue

                # single material
                mat_appended = False
                found_mat = None
                if reuse_materials is True:
                    mat_name = self.get_material_name(chunk.name)
                    if bpy.data.materials.find(mat_name) != -1:
                        found_mat = bpy.data.materials.get(mat_name)

                    if found_mat is not None:
                        b_mats.append(
                            (found_mat, self.is_material_nodraw(chunk.name)))
                        mat_appended = True

                if not mat_appended:
                    b_mats.append((self.create_std_material(chunk, reuse_images),
                                   self.is_material_nodraw(chunk.name)))

            # Deselect all
            if bpy.ops.object.select_all.poll():
                bpy.ops.object.select_all(action="DESELECT")

            view_layer = context.view_layer
            collection = view_layer.active_layer_collection.collection
            new_objects = {}  # put new objects here
            armature_chunk = None
            node_transforms = {}

            # SPLIT_OB_OR_GROUP = bool(use_split_objects or use_split_groups)
            # Create meshes from the data, warning 'vertex_groups' wont suppot splitting
            # ~ print(dataname, user_vnor, use_vtex)

            # parse bone list first.
            for chunk in data.chunks:
                if isinstance(chunk, CgfFormat.BoneNameListChunk):
                    self.parse_bone_name_list(chunk)

            # parse bone data
            for chunk in data.chunks:
                if isinstance(chunk, CgfFormat.BoneAnimChunk):
                    self.build_bone_infos(chunk)
                elif isinstance(chunk, CgfFormat.BoneInitialPosChunk):
                    self.process_bone_initial_position(chunk)

            for chunk in data.chunks:
                if isinstance(chunk, CgfFormat.NodeChunk) and isinstance(chunk.object, CgfFormat.MeshChunk):
                    self.dataname = to_str(chunk.name)
                    self.create_mesh(new_objects,
                                     chunk.object,
                                     b_mats,
                                     self.dataname,
                                     )
                    node_transforms[chunk.object] = Matrix(
                        chunk.transform.as_tuple()).transposed()
                    self.mapping_vertex_group_weights(new_objects)

                elif import_skeleton and isinstance(chunk, CgfFormat.BoneAnimChunk):
                    self.create_armatures(
                        chunk, new_objects, scale_factor=scale_factor)

            # create new obj
            for (chk, obj) in new_objects.items():
                if obj not in collection.objects.values():
                    collection.objects.link(obj)
                    obj.select_set(True)
                    bpy.ops.object.shade_smooth()
                # we could apply this anywhere before scaling
                node_transform = node_transforms[chk] if chk in node_transforms else None
                print('Node transform: %s' % node_transform)
                obj.matrix_world = global_matrix
                if node_transform:
                    obj.matrix_world = obj.matrix_world @ node_transform
                print('Apply obj %s\' matrix world.' % obj)

            for i in collection.objects:
                i.select_set(False)  # deselect all objects
                if i.hide_render is True:
                    i.hide_set(True)

            # Deselect all
            if bpy.ops.object.select_all.poll():
                bpy.ops.object.select_all(action="DESELECT")

            if len(new_objects):
                for obj in new_objects.values():
                    if obj.type == 'ARMATURE':
                        obj.select_set(True)
                        break

            progress.leave_substeps("Done ...")

            if import_animations:
                progress.enter_substeps(
                    4, "Import animations by searching Cry action list file (CAL) ...")
                self.load_animations()
                progress.leave_substeps("Done, imported animations ...")

            progress.leave_substeps("Finished importing CGF %r ..." % filepath)

        # Clean all
        bpy.ops.outliner.orphans_purge()

        return {'FINISHED'}
