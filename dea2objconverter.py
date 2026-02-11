#coding:utf-8
"""
DAE to OBJ Converter - Blender Addon
Converts COLLADA (.dae) files to Wavefront (.obj) format

VERSION HISTORY:
  v1.1.0 - Added namespace-aware XML parsing for broader COLLADA version support,
           material-to-texture mapping via effect chain, recursive texture discovery.
           Now shares conversion logic with dea2obj2import.py.
  v1.0.0 - Initial release with basic COLLADA 1.4.1 support

ARCHITECTURE:
  This addon is a STANDALONE converter that shares core conversion functions with
  dea2obj2import.py (the Blender importer). Both use the same:
    - Material-texture extraction logic
    - XML parsing with namespace awareness
    - OBJ/MTL generation code
  
  Use dea2objconverter.py for:
    - Batch converting DAE files to OBJ on disk
    - External workflows that don't require Blender import
    - Testing/debugging conversion without importing
  
  Use dea2obj2import.py for:
    - Direct import into Blender
    - Automatic texture copying and material assignment
    - Mesh separation and smart naming
"""

bl_info = {
    "name": "DAE to OBJ Converter",
    "author": "MDufJokeAIR",
    "version": (1, 1, 0),
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
# Helper Functions for Texture and UV Map Support
# ============================================================================
# NOTE: The following functions are SHARED between dea2obj2import.py and 
# dea2objconverter.py and must be kept in sync:
#   - _find_texture_file()
#   - _parse_sources()
#   - _parse_vertex_semantics()
#   - _build_material_texture_map()
#   - convert_dae_to_obj()
#   - _triangulate_face()
#
# If you fix a bug or add a feature to any of these functions, please apply
# the SAME FIX to the corresponding function in the other file.
# ============================================================================

def _find_texture_file(base_dir, filename):
    """
    Recursively search for a texture file in base_dir and subdirectories.
    Useful for finding textures in organized folder structures like:
      Custom Color 1/, Custom Color 2/, Normal Color/, etc.
    Returns the full path if found, otherwise returns the basename.
    """
    if not filename or not base_dir:
        return filename
    
    # Check if file exists in base directory
    full_path = os.path.join(base_dir, filename)
    if os.path.exists(full_path):
        return filename
    
    # Search in subdirectories
    try:
        for root, dirs, files in os.walk(base_dir):
            if filename in files:
                rel_path = os.path.relpath(os.path.join(root, filename), base_dir)
                return rel_path
    except:
        pass
    
    # Return basename if not found
    return os.path.basename(filename)


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


def _build_material_texture_map(tree, base_dir=''):
    """
    Walk library_images -> library_effects -> library_materials.
    Returns: mat_id -> (mat_name, tex_filename_or_None), and set of all tex filenames.
    Searches for texture files recursively if base_dir is provided.
    Handles both <init_from>filename</init_from> and <init_from><ref>filename</ref></init_from> formats.
    """
    image_map = {}
    root = tree.getroot()
    
    # Find library_images element with namespace awareness
    lib_images = root.find('{*}library_images')
    if lib_images is not None:
        # Try namespace-aware findall first, then fallback to direct iteration
        images = lib_images.findall('{*}image')
        if not images:
            # Fallback: iterate through all children and match by tag name (ignore namespace)
            images = [child for child in lib_images if child.tag.endswith('image') or child.tag == 'image']
        
        for image in images:
            img_id = image.attrib.get('id', '')
            
            # Try namespace-aware first, then fallback
            init_from = image.find('{*}init_from')
            if init_from is None:
                init_from = image.find('init_from')
            
            if init_from is not None:
                raw = None
                # Try direct text first
                if init_from.text:
                    raw = init_from.text.strip()
                # Try <ref> subelement (COLLADA 1.5 format)
                if not raw:
                    ref = init_from.find('{*}ref')
                    if ref is None:
                        ref = init_from.find('ref')
                    if ref is not None and ref.text:
                        raw = ref.text.strip()
                
                if raw:
                    if raw.startswith('file://'):
                        raw = raw[7:]
                    # Search for texture file in subdirectories
                    tex_file = _find_texture_file(base_dir, os.path.basename(raw))
                    image_map[img_id] = tex_file

    effect_image_map = {}
    lib_effects = root.find('{*}library_effects')
    if lib_effects is not None:
        # Try namespace-aware findall first, then fallback
        effects = lib_effects.findall('{*}effect')
        if not effects:
            effects = [child for child in lib_effects if child.tag.endswith('effect') or child.tag == 'effect']
        
        for effect in effects:
            eff_id = effect.attrib.get('id', '')
            profile = effect.find('{*}profile_COMMON')
            if profile is None:
                profile = effect.find('profile_COMMON')
            
            if profile is None:
                continue
            
            newparams = profile.findall('{*}newparam')
            if not newparams:
                newparams = profile.findall('newparam')
            
            for newparam in newparams:
                surface = newparam.find('{*}surface')
                if surface is None:
                    surface = newparam.find('surface')
                
                if surface is not None:
                    init_from = surface.find('{*}init_from')
                    if init_from is None:
                        init_from = surface.find('init_from')
                    
                    if init_from is not None:
                        raw_img_id = None
                        # Try direct text
                        if init_from.text:
                            raw_img_id = init_from.text.strip()
                        # Try <ref> subelement
                        if not raw_img_id:
                            ref = init_from.find('{*}ref')
                            if ref is None:
                                ref = init_from.find('ref')
                            if ref is not None and ref.text:
                                raw_img_id = ref.text.strip()
                        
                        if raw_img_id:
                            effect_image_map[eff_id] = raw_img_id
                            break

    mat_tex_map = {}
    all_textures = set()
    lib_materials = root.find('{*}library_materials')
    if lib_materials is not None:
        # Try namespace-aware findall first, then fallback
        materials = lib_materials.findall('{*}material')
        if not materials:
            materials = [child for child in lib_materials if child.tag.endswith('material') or child.tag == 'material']
        
        for material in materials:
            mat_id = material.attrib.get('id', '')
            mat_name = material.attrib.get('name', mat_id)
            inst = material.find('{*}instance_effect')
            if inst is None:
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
    Convert a DAE file to OBJ + MTL format with full UV map support.

    Features:
    - Handles <triangles> and <polylist> (mixed tri/quad faces via <vcount>)
    - Unified vertex format: POSITION, TEXCOORD, NORMAL all in <vertices>
    - Separate per-primitive TEXCOORD / NORMAL inputs (classic format)
    - Multiple materials with per-polylist usemtl entries
    - Recursive texture file search in subdirectories
    - Enhanced MTL material properties for better texture application
    """
    try:
        tree = ET.ElementTree(file=input_filepath)
        input_dir = os.path.dirname(input_filepath)

        # Strip COLLADA namespace
        for el in tree.iter():
            if '}' in el.tag:
                el.tag = el.tag.split('}', 1)[1]

        # Build material map with recursive texture search
        mat_tex_map, all_textures = _build_material_texture_map(tree, input_dir)

        meshes = tree.findall('library_geometries/geometry/mesh')
        if not meshes:
            return False, "No mesh geometry found in DAE file (enhanced UV map support)"

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

                # Collect all primitives first to gather all UV/normal sources
                prims = mesh.findall('triangles') + mesh.findall('polylist')
                all_uv_sources = {vtx_sem.get('TEXCOORD')} if vtx_sem.get('TEXCOORD') else {None}
                all_norm_sources = {vtx_sem.get('NORMAL')} if vtx_sem.get('NORMAL') else {None}
                
                for prim in prims:
                    for inp in prim.findall('input'):
                        sem = inp.attrib['semantic']
                        if sem not in ('VERTEX',):
                            src = inp.attrib.get('source', '').lstrip('#')
                            if sem == 'TEXCOORD':
                                all_uv_sources.add(src)
                            elif sem == 'NORMAL':
                                all_norm_sources.add(src)

                obj_f.write(f"o Mesh_{mesh_idx}\n")
                for v  in pos_data:  obj_f.write('v  %.4f %.4f %.4f\n' % tuple(v[:3]))
                for vn in norm_data: obj_f.write('vn %.4f %.4f %.4f\n' % tuple(vn[:3]))
                for uv in uv_data:   obj_f.write('vt %.4f %.4f\n' % (uv[0], uv[1]))

                # Track offsets for all UV/normal sources
                prim_uv_offsets = {vtx_sem.get('TEXCOORD'): gvt}
                prim_norm_offsets = {vtx_sem.get('NORMAL'): gvn}
                current_vt = gvt + len(uv_data)
                current_vn = gvn + len(norm_data)
                
                for uv_src in all_uv_sources:
                    if uv_src and uv_src not in prim_uv_offsets:
                        uv_src_data = sources.get(uv_src, [])
                        prim_uv_offsets[uv_src] = current_vt
                        for uv in uv_src_data:
                            obj_f.write('vt %.4f %.4f\n' % (uv[0], uv[1]))
                        current_vt += len(uv_src_data)
                
                for norm_src in all_norm_sources:
                    if norm_src and norm_src not in prim_norm_offsets:
                        norm_src_data = sources.get(norm_src, [])
                        prim_norm_offsets[norm_src] = current_vn
                        for vn in norm_src_data:
                            obj_f.write('vn %.4f %.4f %.4f\n' % tuple(vn[:3]))
                        current_vn += len(norm_src_data)

                has_uv = len(uv_data) > 0
                has_vn = len(norm_data) > 0

                for prim in prims:
                    mat_id = prim.attrib.get('material', '')
                    mat_name, _ = mat_tex_map.get(mat_id, (mat_id or 'Material_001', None))
                    obj_f.write(f"usemtl {mat_name}\ns off\n")

                    p_el = prim.find('p')
                    if p_el is None or not p_el.text:
                        continue
                    p_idx = list(map(int, p_el.text.split()))

                    prim_inputs = prim.findall('input')
                    if not prim_inputs:
                        continue
                    p_stride = max(int(inp.attrib.get('offset', 0)) for inp in prim_inputs) + 1
                    prim_off  = {inp.attrib['semantic']: int(inp.attrib.get('offset', 0))
                                 for inp in prim_inputs}
                    prim_src  = {inp.attrib['semantic']: inp.attrib.get('source', '').lstrip('#')
                                 for inp in prim_inputs if inp.attrib['semantic'] != 'VERTEX'}

                    prim_uv_src = prim_src.get('TEXCOORD', vtx_sem.get('TEXCOORD') if unified else None)
                    prim_norm_src = prim_src.get('NORMAL', vtx_sem.get('NORMAL') if unified else None)
                    
                    p_uv_data   = sources.get(prim_uv_src, []) if prim_uv_src else []
                    p_norm_data = sources.get(prim_norm_src, []) if prim_norm_src else []
                    p_has_uv = len(p_uv_data)   > 0
                    p_has_vn = len(p_norm_data)  > 0
                    
                    prim_uv_offset = prim_uv_offsets.get(prim_uv_src, gvt)
                    prim_norm_offset = prim_norm_offsets.get(prim_norm_src, gvn)

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
                                
                                if unified:
                                    vt_idx = vi + 1 + prim_uv_offset if p_has_uv else None
                                    vn_idx = vi + 1 + prim_norm_offset if p_has_vn else None
                                else:
                                    vt_raw = chunk[prim_off['TEXCOORD']] if 'TEXCOORD' in prim_off else None
                                    vn_raw = chunk[prim_off['NORMAL']]   if 'NORMAL'   in prim_off else None
                                    vt_idx = (vt_raw + 1 + prim_uv_offset) if vt_raw is not None else None
                                    vn_idx = (vn_raw + 1 + prim_norm_offset) if vn_raw is not None else None

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
                gvt = current_vt
                gvn = current_vn

        # Write enhanced MTL file
        with open(mtl_path, 'w') as mtl_f:
            mtl_f.write("# Enhanced MTL file with texture support\n")
            mtl_f.write("# Generated from COLLADA via OBJ conversion\n")
            mtl_f.write("# Supports reuse of textures across color variants\n\n")
            
            written = set()
            for _, (mat_name, tex_file) in mat_tex_map.items():
                if mat_name in written:
                    continue
                written.add(mat_name)
                mtl_f.write(f"newmtl {mat_name}\n")
                mtl_f.write("Ka 1.0 1.0 1.0\n")  # Ambient
                mtl_f.write("Kd 1.0 1.0 1.0\n")  # Diffuse
                mtl_f.write("Ks 0.5 0.5 0.5\n")  # Specular (slightly reflective)
                mtl_f.write("Ns 32.0\n")         # Shininess
                mtl_f.write("d 1.0\n")            # Transparency
                mtl_f.write("illum 2\n")         # Illumination model (with highlights)
                if tex_file:
                    mtl_f.write(f"map_Kd {tex_file}\n")  # Diffuse texture
                    mtl_f.write(f"map_bump {tex_file}\n")  # Bump map (same texture)
                mtl_f.write("\n")
            
            if not written:
                mtl_f.write("newmtl Material_001\n")
                mtl_f.write("Ka 1.0 1.0 1.0\nKd 1.0 1.0 1.0\nKs 0.5 0.5 0.5\nNs 32.0\nillum 2\n")

        return True, "Conversion successful! (UV maps and textures included)"

    except Exception as e:
        import traceback
        traceback.print_exc()
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
        
        # Convert with enhanced UV map support
        success, message = convert_dae_to_obj(input_path, output_path)
        
        if success:
            self.report({'INFO'}, f"Converted with UV maps!: {output_path}")
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