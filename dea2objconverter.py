#coding:utf-8
"""
DAE to OBJ Converter - Blender Addon
Converts COLLADA (.dae) files to Wavefront (.obj) format
"""

bl_info = {
    "name": "DAE to OBJ Converter",
    "author": "Your Name",
    "version": (1, 0, 0),
    "blender": (2, 80, 0),
    "location": "View3D > Sidebar > DAE Converter",
    "description": "Convert COLLADA (.dae) files to Wavefront (.obj) format",
    "category": "Import-Export",
}

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel
import os

try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


# ============================================================================
# Conversion Function
# ============================================================================

def _parse_sources(mesh):
    """Parse all <source> elements into a dict: source_id -> list of tuples."""
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
    """Return dict: semantic -> source_id for the <vertices> element."""
    result = {}
    if vertices_el is not None:
        for inp in vertices_el.findall('input'):
            result[inp.attrib['semantic']] = inp.attrib['source'].lstrip('#')
    return result


def _build_material_texture_map(tree):
    """
    Walk library_images -> library_effects -> library_materials.
    Returns: mat_id -> (mat_name, tex_filename_or_None), and set of all tex filenames.
    """
    image_map = {}
    for image in tree.findall('library_images/image'):
        img_id = image.attrib.get('id', '')
        init_from = image.find('init_from')
        if init_from is not None and init_from.text:
            raw = init_from.text.strip()
            if raw.startswith('file://'):
                raw = raw[7:]
            image_map[img_id] = os.path.basename(raw)

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


def _triangulate_face(n):
    """Fan-triangulate a face with n vertices, returning list of (i,j,k) index triples."""
    return [(0, i, i + 1) for i in range(1, n - 1)]


def convert_dae_to_obj(input_filepath, output_filepath):
    """
    Convert a DAE file to OBJ + MTL format.

    Handles:
    - <triangles> and <polylist> (mixed tri/quad faces via <vcount>)
    - Unified vertex format: POSITION, TEXCOORD, NORMAL all in <vertices>
    - Separate per-primitive TEXCOORD / NORMAL inputs (classic format)
    - Multiple materials with per-polylist usemtl entries
    """
    try:
        tree = ET.ElementTree(file=input_filepath)

        # Strip COLLADA namespace
        for el in tree.iter():
            if '}' in el.tag:
                el.tag = el.tag.split('}', 1)[1]

        mat_tex_map, all_textures = _build_material_texture_map(tree)

        meshes = tree.findall('library_geometries/geometry/mesh')
        if not meshes:
            return False, "No mesh geometry found in DAE file"

        base_mtl = os.path.splitext(os.path.basename(output_filepath))[0] + ".mtl"
        mtl_path = os.path.splitext(output_filepath)[0] + ".mtl"

        with open(output_filepath, 'w') as obj_f:
            obj_f.write(f"mtllib {base_mtl}\n")

            gv = gvt = gvn = 0  # global offsets

            for mesh_idx, mesh in enumerate(meshes):
                sources   = _parse_sources(mesh)
                vtx_sem   = _parse_vertex_semantics(mesh.find('vertices'))

                pos_data  = sources.get(vtx_sem.get('POSITION', ''), [])
                uv_data   = sources.get(vtx_sem.get('TEXCOORD',  ''), [])
                norm_data = sources.get(vtx_sem.get('NORMAL',    ''), [])
                unified   = vtx_sem.get('TEXCOORD') is not None or vtx_sem.get('NORMAL') is not None

                obj_f.write(f"o Mesh_{mesh_idx}\n")
                for v  in pos_data:  obj_f.write('v  %.4f %.4f %.4f\n' % tuple(v[:3]))
                for vn in norm_data: obj_f.write('vn %.4f %.4f %.4f\n' % tuple(vn[:3]))
                for uv in uv_data:   obj_f.write('vt %.4f %.4f\n' % (uv[0], 1.0 - uv[1]))

                has_uv = len(uv_data) > 0
                has_vn = len(norm_data) > 0

                for prim in mesh.findall('triangles') + mesh.findall('polylist'):
                    mat_id = prim.attrib.get('material', '')
                    mat_name, _ = mat_tex_map.get(mat_id, (mat_id or 'Material_001', None))
                    obj_f.write(f"usemtl {mat_name}\ns off\n")

                    p_el = prim.find('p')
                    if p_el is None or not p_el.text:
                        continue
                    p_idx = list(map(int, p_el.text.split()))

                    prim_inputs = prim.findall('input')
                    p_stride = max(int(inp.attrib.get('offset', 0)) for inp in prim_inputs) + 1
                    prim_off  = {inp.attrib['semantic']: int(inp.attrib.get('offset', 0))
                                 for inp in prim_inputs}
                    prim_src  = {inp.attrib['semantic']: inp.attrib.get('source', '').lstrip('#')
                                 for inp in prim_inputs if inp.attrib['semantic'] != 'VERTEX'}

                    p_uv_data   = sources.get(prim_src.get('TEXCOORD', ''), uv_data   if unified else [])
                    p_norm_data = sources.get(prim_src.get('NORMAL',   ''), norm_data if unified else [])
                    p_has_uv = len(p_uv_data)   > 0
                    p_has_vn = len(p_norm_data)  > 0

                    if prim.tag == 'polylist':
                        vc_el = prim.find('vcount')
                        vcount = list(map(int, vc_el.text.split())) if vc_el is not None else []
                    else:
                        vcount = [3] * int(prim.attrib.get('count', 0))

                    pos = 0
                    for vc in vcount:
                        raw = [p_idx[pos + j * p_stride : pos + j * p_stride + p_stride]
                               for j in range(vc)]
                        pos += vc * p_stride

                        for (i0, i1, i2) in _triangulate_face(vc):
                            parts = []
                            for chunk in (raw[i0], raw[i1], raw[i2]):
                                vi = chunk[prim_off.get('VERTEX', 0)]
                                v_idx  = vi + 1 + gv
                                vt_idx = vi + 1 + gvt if p_has_uv else None
                                vn_idx = vi + 1 + gvn if p_has_vn else None

                                if p_has_uv and p_has_vn:
                                    parts.append(f"{v_idx}/{vt_idx}/{vn_idx}")
                                elif p_has_uv:
                                    parts.append(f"{v_idx}/{vt_idx}")
                                elif p_has_vn:
                                    parts.append(f"{v_idx}//{vn_idx}")
                                else:
                                    parts.append(f"{v_idx}")

                            obj_f.write("f " + " ".join(parts) + "\n")

                gv  += len(pos_data)
                gvt += len(uv_data)
                gvn += len(norm_data)

        # Write MTL
        with open(mtl_path, 'w') as mtl_f:
            written = set()
            for _, (mat_name, tex_file) in mat_tex_map.items():
                if mat_name in written:
                    continue
                written.add(mat_name)
                mtl_f.write(f"newmtl {mat_name}\n")
                mtl_f.write("Ka 1.0 1.0 1.0\nKd 1.0 1.0 1.0\nKs 0.0 0.0 0.0\nd 1.0\nillum 1\n")
                if tex_file:
                    mtl_f.write(f"map_Kd {tex_file}\n")
                mtl_f.write("\n")
            if not written:
                mtl_f.write("newmtl Material_001\nKa 1.0 1.0 1.0\nKd 1.0 1.0 1.0\n")

        return True, "Conversion successful!"

    except Exception as e:
        return False, f"Error during conversion: {str(e)}"


# ============================================================================
# Blender Operators
# ============================================================================

class DAE_OT_ConvertToObj(Operator):
    """Convert DAE file to OBJ format"""
    bl_idname = "dae.convert_to_obj"
    bl_label = "Convert .dae to .obj"
    bl_options = {'REGISTER'}
    
    filepath: StringProperty(
        name="Input File",
        description="Select a DAE file to convert",
        subtype='FILE_PATH'
    )
    
    filter_glob: StringProperty(
        default="*.dae",
        options={'HIDDEN'}
    )
    
    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "No file selected")
            return {'CANCELLED'}
        
        # Generate output filename
        input_path = bpy.path.abspath(self.filepath)
        output_path = os.path.splitext(input_path)[0] + ".obj"
        
        # Convert
        success, message = convert_dae_to_obj(input_path, output_path)
        
        if success:
            self.report({'INFO'}, f"Converted successfully to: {output_path}")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# ============================================================================
# UI Panel
# ============================================================================

class DAE_PT_ConverterPanel(Panel):
    """Creates a Panel in the 3D Viewport sidebar"""
    bl_label = "DAE Converter"
    bl_idname = "DAE_PT_converter_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DAE Converter'
    
    def draw(self, context):
        layout = self.layout
        
        # Title
        row = layout.row()
        row.label(text="COLLADA to OBJ Converter", icon='EXPORT')
        
        layout.separator()
        
        # Convert button
        row = layout.row()
        row.scale_y = 2.0
        row.operator("dae.convert_to_obj", text="Convert .dae to .obj", icon='FILE_TICK')
        
        layout.separator()
        
        # Instructions
        box = layout.box()
        box.label(text="Instructions:", icon='INFO')
        box.label(text="1. Click the button above")
        box.label(text="2. Select your .dae file")
        box.label(text="3. .obj file will be created")
        box.label(text="   in the same folder")


# ============================================================================
# Registration
# ============================================================================

classes = (
    DAE_OT_ConvertToObj,
    DAE_PT_ConverterPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()