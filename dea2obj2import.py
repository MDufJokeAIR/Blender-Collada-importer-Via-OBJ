#coding:utf-8
"""
DAE to OBJ Importer with Texture Support - Blender Addon
Imports COLLADA (.dae) files by converting to OBJ with MTL generation
"""

bl_info = {
    "name": "Import COLLADA via OBJ (w/ Textures)",
    "author": "Your Name",
    "version": (1, 1, 0),
    "blender": (2, 80, 0),
    "location": "File > Import > COLLADA (.dae)",
    "description": "Import COLLADA (.dae) files converting to OBJ+MTL",
    "category": "Import-Export",
}

import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper
from bpy.types import Operator
import os
import tempfile
import shutil

try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


# ============================================================================
# Conversion Function
# ============================================================================

def _parse_sources(mesh):
    """Parse all <source> elements of a mesh into a dict: source_id -> list of tuples."""
    result = {}
    for source in mesh.findall('source'):
        src_id = source.attrib['id']
        float_array = source.find('float_array')
        if float_array is None:
            continue
        floats = list(map(float, float_array.text.split()))
        accessor = source.find('technique_common/accessor')
        stride = int(accessor.attrib.get('stride', 3)) if accessor is not None else 3
        result[src_id] = [floats[i:i+stride] for i in range(0, len(floats), stride)]
    return result


def _parse_vertex_semantics(vertices_el):
    """Return a dict: semantic -> source_id for the <vertices> element."""
    result = {}
    if vertices_el is not None:
        for inp in vertices_el.findall('input'):
            result[inp.attrib['semantic']] = inp.attrib['source'].lstrip('#')
    return result


def _build_material_texture_map(tree):
    """
    Walk library_images -> library_effects -> library_materials to produce:
      material_id -> (material_name, texture_filename_or_None)
    Returns also a set of all unique texture filenames found.
    """
    # image_id -> filename
    image_map = {}
    for image in tree.findall('library_images/image'):
        img_id = image.attrib.get('id', '')
        init_from = image.find('init_from')
        if init_from is not None and init_from.text:
            raw = init_from.text.strip()
            if raw.startswith('file://'):
                raw = raw[7:]
            image_map[img_id] = os.path.basename(raw)

    # effect_id -> image_id  (first surface init_from wins)
    effect_image_map = {}
    for effect in tree.findall('library_effects/effect'):
        eff_id = effect.attrib.get('id', '')
        profile = effect.find('profile_COMMON')
        if profile is None:
            continue
        for newparam in profile.findall('newparam'):
            surface = newparam.find('surface')
            if surface is not None:
                init_from = surface.find('init_from')
                if init_from is not None and init_from.text:
                    effect_image_map[eff_id] = init_from.text.strip()
                    break

    # material_id -> (name, filename or None)
    mat_tex_map = {}
    all_textures = set()
    for material in tree.findall('library_materials/material'):
        mat_id = material.attrib.get('id', '')
        mat_name = material.attrib.get('name', mat_id)
        inst = material.find('instance_effect')
        tex_file = None
        if inst is not None:
            eff_id = inst.attrib.get('url', '').lstrip('#')
            img_id = effect_image_map.get(eff_id)
            if img_id:
                tex_file = image_map.get(img_id)
        mat_tex_map[mat_id] = (mat_name, tex_file)
        if tex_file:
            all_textures.add(tex_file)

    return mat_tex_map, all_textures


def _triangulate_face(verts):
    """Fan-triangulate a polygon given as a list of vertex indices."""
    tris = []
    for i in range(1, len(verts) - 1):
        tris.append([verts[0], verts[i], verts[i + 1]])
    return tris


def convert_dae_to_obj(input_filepath, output_filepath):
    """
    Convert a DAE file to OBJ + MTL format.

    Handles:
    - <triangles> and <polylist> (including mixed tri/quad faces)
    - Unified vertex format: POSITION, TEXCOORD, NORMAL all in <vertices>
    - Separate per-primitive TEXCOORD / NORMAL inputs (classic format)
    - Multiple materials with per-polylist usemtl assignment
    """
    try:
        tree = ET.ElementTree(file=input_filepath)

        # Strip COLLADA namespace from all tags
        for el in tree.iter():
            if '}' in el.tag:
                el.tag = el.tag.split('}', 1)[1]

        # Build material -> (name, texture) mapping
        mat_tex_map, all_textures = _build_material_texture_map(tree)

        meshes = tree.findall('library_geometries/geometry/mesh')
        if not meshes:
            return False, "No mesh geometry found in DAE file", set()

        base_mtl = os.path.splitext(os.path.basename(output_filepath))[0] + ".mtl"
        mtl_path = os.path.splitext(output_filepath)[0] + ".mtl"

        with open(output_filepath, 'w') as obj_f:
            obj_f.write(f"mtllib {base_mtl}\n")

            global_v_offset = 0
            global_vt_offset = 0
            global_vn_offset = 0

            for mesh_idx, mesh in enumerate(meshes):
                sources = _parse_sources(mesh)
                vertices_el = mesh.find('vertices')
                vertex_sem = _parse_vertex_semantics(vertices_el)

                # Determine position / UV / normal arrays
                pos_id  = vertex_sem.get('POSITION')
                uv_id   = vertex_sem.get('TEXCOORD')
                norm_id = vertex_sem.get('NORMAL')

                pos_data  = sources.get(pos_id,  [])
                uv_data   = sources.get(uv_id,   [])
                norm_data = sources.get(norm_id, [])

                # Unified vertex: all semantics share a single index in <p>
                unified = (uv_id is not None or norm_id is not None)

                # Write vertex data
                obj_f.write(f"o Mesh_{mesh_idx}\n")
                for v in pos_data:
                    obj_f.write('v %.4f %.4f %.4f\n' % tuple(v[:3]))
                for vn in norm_data:
                    obj_f.write('vn %.4f %.4f %.4f\n' % tuple(vn[:3]))
                for uv in uv_data:
                    u, v = uv[0], uv[1]
                    obj_f.write('vt %.4f %.4f\n' % (u, 1.0 - v))  # flip V for Blender

                has_uv = len(uv_data) > 0
                has_vn = len(norm_data) > 0

                # Collect all primitive elements: <triangles> and <polylist>
                primitives = mesh.findall('triangles') + mesh.findall('polylist')

                for prim in primitives:
                    mat_id = prim.attrib.get('material', '')
                    mat_name, tex_file = mat_tex_map.get(mat_id, (mat_id or 'Material_001', None))
                    obj_f.write(f"usemtl {mat_name}\n")
                    obj_f.write("s off\n")

                    p_el = prim.find('p')
                    if p_el is None or not p_el.text:
                        continue
                    p_indices = list(map(int, p_el.text.split()))

                    # Build per-vertex stride from inputs declared in the primitive
                    prim_inputs = prim.findall('input')
                    if not prim_inputs:
                        continue
                    p_stride = max(int(inp.attrib.get('offset', 0)) for inp in prim_inputs) + 1

                    # Map semantic -> offset within one vertex's p-chunk
                    # For non-unified: TEXCOORD / NORMAL have their own offsets
                    # For unified: only VERTEX at offset 0
                    prim_offset = {}
                    prim_src = {}
                    for inp in prim_inputs:
                        sem = inp.attrib['semantic']
                        off = int(inp.attrib.get('offset', 0))
                        prim_offset[sem] = off
                        if sem not in ('VERTEX',):
                            prim_src[sem] = inp.attrib.get('source', '').lstrip('#')

                    # Override UV/normal arrays if the primitive declares its own
                    prim_uv_data   = sources.get(prim_src.get('TEXCOORD', ''), uv_data   if unified else [])
                    prim_norm_data = sources.get(prim_src.get('NORMAL',   ''), norm_data if unified else [])
                    prim_has_uv = len(prim_uv_data) > 0
                    prim_has_vn = len(prim_norm_data) > 0

                    # Determine face vertex counts
                    is_polylist = (prim.tag == 'polylist')
                    if is_polylist:
                        vcount_el = prim.find('vcount')
                        vcount = list(map(int, vcount_el.text.split())) if vcount_el is not None else []
                    else:
                        # <triangles>: every face has 3 verts
                        count = int(prim.attrib.get('count', 0))
                        vcount = [3] * count

                    # Parse faces
                    p_pos = 0
                    for vc in vcount:
                        # Read raw vertex index chunks for this face
                        raw_verts = []
                        for _ in range(vc):
                            chunk = p_indices[p_pos : p_pos + p_stride]
                            p_pos += p_stride
                            raw_verts.append(chunk)

                        # Triangulate
                        tri_groups = _triangulate_face(list(range(vc)))

                        for tri in tri_groups:
                            parts = []
                            for vi in tri:
                                chunk = raw_verts[vi]
                                v_raw = chunk[prim_offset.get('VERTEX', 0)]

                                if unified:
                                    # All attributes share the same index
                                    v_idx  = v_raw + 1 + global_v_offset
                                    vt_idx = v_raw + 1 + global_vt_offset if prim_has_uv else None
                                    vn_idx = v_raw + 1 + global_vn_offset if prim_has_vn else None
                                else:
                                    v_idx  = v_raw + 1 + global_v_offset
                                    vt_raw = chunk[prim_offset['TEXCOORD']] if 'TEXCOORD' in prim_offset else None
                                    vn_raw = chunk[prim_offset['NORMAL']]   if 'NORMAL'   in prim_offset else None
                                    vt_idx = (vt_raw + 1 + global_vt_offset) if vt_raw is not None else None
                                    vn_idx = (vn_raw + 1 + global_vn_offset) if vn_raw is not None else None

                                if prim_has_uv and prim_has_vn:
                                    parts.append(f"{v_idx}/{vt_idx}/{vn_idx}")
                                elif prim_has_uv:
                                    parts.append(f"{v_idx}/{vt_idx}")
                                elif prim_has_vn:
                                    parts.append(f"{v_idx}//{vn_idx}")
                                else:
                                    parts.append(f"{v_idx}")

                            obj_f.write("f " + " ".join(parts) + "\n")

                global_v_offset  += len(pos_data)
                global_vt_offset += len(uv_data)
                global_vn_offset += len(norm_data)

        # Write MTL file with one entry per material
        with open(mtl_path, 'w') as mtl_f:
            written = set()
            for mat_id, (mat_name, tex_file) in mat_tex_map.items():
                if mat_name in written:
                    continue
                written.add(mat_name)
                mtl_f.write(f"newmtl {mat_name}\n")
                mtl_f.write("Ka 1.0 1.0 1.0\n")
                mtl_f.write("Kd 1.0 1.0 1.0\n")
                mtl_f.write("Ks 0.0 0.0 0.0\n")
                mtl_f.write("d 1.0\n")
                mtl_f.write("illum 1\n")
                if tex_file:
                    mtl_f.write(f"map_Kd {tex_file}\n")
                mtl_f.write("\n")
            if not written:
                # Fallback single material
                mtl_f.write("newmtl Material_001\n")
                mtl_f.write("Ka 1.0 1.0 1.0\nKd 1.0 1.0 1.0\n")

        # First texture found (for caller to copy)
        first_tex = next(iter(all_textures), None)
        return True, "Conversion successful!", all_textures

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"Error: {str(e)}", set()


# ============================================================================
# Import Operator
# ============================================================================

class ImportDAE(Operator, ImportHelper):
    """Import COLLADA (.dae) file"""
    bl_idname = "import_scene.dae_via_obj"
    bl_label = "Import COLLADA (.dae)"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".dae"
    filter_glob: StringProperty(
        default="*.dae",
        options={'HIDDEN'}
    )
    
    use_temp_file: BoolProperty(
        name="Use Temporary File",
        description="Convert to temporary OBJ file (recommended)",
        default=True
    )
    
    def execute(self, context):
        input_path = bpy.path.abspath(self.filepath)
        input_dir = os.path.dirname(input_path)
        
        # Determine output path
        if self.use_temp_file:
            temp_dir = tempfile.gettempdir()
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(temp_dir, base_name + "_temp.obj")
            mtl_path = os.path.join(temp_dir, base_name + "_temp.mtl")
        else:
            output_path = os.path.splitext(input_path)[0] + "_converted.obj"
            mtl_path = os.path.splitext(input_path)[0] + "_converted.mtl"
        
        # Convert DAE to OBJ
        self.report({'INFO'}, "Converting DAE to OBJ...")
        success, message, all_textures = convert_dae_to_obj(input_path, output_path)
        
        if not success:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        
        # Copy ALL textures to temp folder if needed so OBJ importer finds them
        copied_textures = []
        if self.use_temp_file:
            for tex_name in all_textures:
                src_tex = os.path.join(input_dir, tex_name)
                dst_tex = os.path.join(tempfile.gettempdir(), tex_name)
                if os.path.exists(src_tex):
                    try:
                        shutil.copy(src_tex, dst_tex)
                        copied_textures.append(dst_tex)
                    except:
                        pass
        
        # Import the OBJ file using Blender's native importer
        try:
            if bpy.app.version >= (4, 0, 0):
                bpy.ops.wm.obj_import(filepath=output_path)
            else:
                bpy.ops.import_scene.obj(filepath=output_path)
            
            # Clean up temporary files
            if self.use_temp_file:
                try:
                    os.remove(output_path)
                    os.remove(mtl_path)
                    for dst_tex in copied_textures:
                        if os.path.exists(dst_tex):
                            os.remove(dst_tex)
                except:
                    pass
            
            self.report({'INFO'}, f"Successfully imported: {os.path.basename(input_path)}")
            return {'FINISHED'}
            
        except Exception as e:
            self.report({'ERROR'}, f"Error importing OBJ: {str(e)}")
            return {'CANCELLED'}


# ============================================================================
# Menu Integration
# ============================================================================

def menu_func_import(self, context):
    self.layout.operator(ImportDAE.bl_idname, text="COLLADA Custom (.dae)")


classes = (
    ImportDAE,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()