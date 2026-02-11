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
import time

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


def _find_texture_file(base_dir, filename):
    """
    Recursively search for a texture file in base_dir and subdirectories.
    Returns the full path if found, otherwise returns the basename.
    """
    if not filename or not base_dir:
        return filename
    
    # Check if file exists in base directory
    full_path = os.path.join(base_dir, filename)
    if os.path.exists(full_path):
        return filename
    
    # Search in subdirectories (e.g., Custom Color 1/, Normal Color/, etc.)
    try:
        for root, dirs, files in os.walk(base_dir):
            if filename in files:
                rel_path = os.path.relpath(os.path.join(root, filename), base_dir)
                return rel_path
    except:
        pass
    
    # Return basename if not found (OBJ importer will look in same dir)
    return os.path.basename(filename)


def _build_material_texture_map(tree, base_dir=''):
    """
    Walk library_images -> library_effects -> library_materials to produce:
      material_id -> (material_name, texture_filename_or_None)
    Returns also a set of all unique texture filenames found.
    Searches for texture files recursively if base_dir is provided.
    Handles both <init_from>filename</init_from> and <init_from><ref>filename</ref></init_from> formats.
    """
    print(f"\n=== _build_material_texture_map DEBUG ===")
    
    # Debug: show root element and all top-level children
    root = tree.getroot()
    print(f"Root element: {root.tag}")
    print(f"Root children: {[child.tag for child in root]}")
    
    # image_id -> filename
    image_map = {}
    print(f"Searching for 'library_images' elements...")
    
    # Try multiple approaches to find library_images
    lib_images_variants = [
        tree.findall('library_images'),
        tree.findall('{*}library_images'),
        root.findall('library_images'),
        root.findall('{*}library_images'),
    ]
    
    lib_images = None
    for variant in lib_images_variants:
        if variant:
            print(f"  Found library_images: {variant}")
            lib_images = variant[0]
            break
    
    if lib_images is None:
        print(f"  ERROR: Could not find library_images element!")
        print(f"  Available top-level elements: {[child.tag for child in root]}")
    else:
        print(f"Using library_images: {lib_images.tag}")
        print(f"Direct children of library_images: {[child.tag for child in lib_images]}")
        print(f"Extracting library_images:")
        
        # Try namespace-aware findall first, then fallback to direct iteration by tag name pattern
        images = lib_images.findall('{*}image')
        if not images:
            # Fallback: iterate through all children and match by tag name (ignore namespace)
            print("  findall('{*}image') returned empty, using fallback iteration...")
            images = [child for child in lib_images if child.tag.endswith('image') or child.tag == 'image']
            print(f"  Fallback found {len(images)} image elements")
        
        for image in images:
            img_id = image.attrib.get('id', '')
            print(f"  Processing image: id='{img_id}'")
            
            # Try namespace-aware first, then fallback
            init_from = image.find('{*}init_from')
            if init_from is None:
                init_from = image.find('init_from')
            
            if init_from is not None:
                print(f"    Found init_from element")
                raw = None
                # Try direct text first
                if init_from.text:
                    raw = init_from.text.strip()
                    print(f"    Got text directly: {raw}")
                # Try <ref> subelement (COLLADA 1.5 format)
                if not raw:
                    ref = init_from.find('{*}ref')
                    if ref is None:
                        ref = init_from.find('ref')
                    if ref is not None and ref.text:
                        raw = ref.text.strip()
                        print(f"    Got from <ref> subelement: {raw}")
                
                if raw:
                    if raw.startswith('file://'):
                        raw = raw[7:]
                    # Search for texture file in subdirectories
                    tex_file = _find_texture_file(base_dir, os.path.basename(raw))
                    image_map[img_id] = tex_file
                    print(f"  id='{img_id}' -> filename='{tex_file}'")
                else:
                    print(f"  id='{img_id}' -> NO texture filename found in init_from")
            else:
                print(f"  id='{img_id}' -> NO init_from found")

    # effect_id -> image_id  (first surface init_from wins)
    effect_image_map = {}
    print("\nSearching for 'library_effects' elements...")
    
    lib_effects_variants = [
        tree.findall('library_effects'),
        tree.findall('{*}library_effects'),
        root.findall('library_effects'),
        root.findall('{*}library_effects'),
    ]
    
    lib_effects = None
    for variant in lib_effects_variants:
        if variant:
            lib_effects = variant[0]
            break
    
    if lib_effects is None:
        print(f"  ERROR: Could not find library_effects element!")
    else:
        print("Extracting library_effects:")
        
        # Try namespace-aware findall first, then fallback
        effects = lib_effects.findall('{*}effect')
        if not effects:
            effects = [child for child in lib_effects if child.tag.endswith('effect') or child.tag == 'effect']
        
        for effect in effects:
            eff_id = effect.attrib.get('id', '')
            eff_name = effect.attrib.get('name', eff_id)
            print(f"  Effect id='{eff_id}', name='{eff_name}'")
            
            # Try namespace-aware first, then fallback
            profile = effect.find('{*}profile_COMMON')
            if profile is None:
                profile = effect.find('profile_COMMON')
            
            if profile is None:
                print(f"    NO profile_COMMON")
                continue
            
            found_image = False
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
                            found_image = True
                            print(f"    Found image reference: '{raw_img_id}'")
                            break
            
            if not found_image:
                print(f"    NO image reference found in any newparam")

    # material_id -> (name, filename or None)
    mat_tex_map = {}
    all_textures = set()
    print("\nSearching for 'library_materials' elements...")
    
    lib_materials_variants = [
        tree.findall('library_materials'),
        tree.findall('{*}library_materials'),
        root.findall('library_materials'),
        root.findall('{*}library_materials'),
    ]
    
    lib_materials = None
    for variant in lib_materials_variants:
        if variant:
            lib_materials = variant[0]
            break
    
    if lib_materials is None:
        print(f"  ERROR: Could not find library_materials element!")
    else:
        print("Extracting library_materials:")
        
        # Try namespace-aware findall first, then fallback
        materials = lib_materials.findall('{*}material')
        if not materials:
            materials = [child for child in lib_materials if child.tag.endswith('material') or child.tag == 'material']
        
        for material in materials:
            mat_id = material.attrib.get('id', '')
            mat_name = material.attrib.get('name', mat_id)
            print(f"  Material id='{mat_id}', name='{mat_name}'")
            
            inst = material.find('{*}instance_effect')
            if inst is None:
                inst = material.find('instance_effect')
            
            tex_file = None
            if inst is not None:
                eff_id = inst.attrib.get('url', '').lstrip('#')
                print(f"    References effect: '{eff_id}'")
                
                img_id = effect_image_map.get(eff_id)
                if img_id:
                    print(f"    Effect maps to image_id: '{img_id}'")
                    tex_file = image_map.get(img_id)
                    if tex_file:
                        print(f"    Image maps to texture: '{tex_file}'")
                    else:
                        print(f"    ERROR: Image_id '{img_id}' not found in image_map!")
                else:
                    print(f"    ERROR: Effect_id '{eff_id}' not found in effect_image_map!")
            else:
                print(f"    NO instance_effect found")
            
            mat_tex_map[mat_id] = (mat_name, tex_file)
            if tex_file:
                all_textures.add(tex_file)
    
    print(f"\nFinal material->texture map: {mat_tex_map}")
    print(f"All textures: {all_textures}")
    print(f"=== End _build_material_texture_map ===\n")

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

        input_dir = os.path.dirname(input_filepath)
        # Re-build material map with texture file search
        mat_tex_map, all_textures = _build_material_texture_map(tree, input_dir)

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

                # Collect all primitive elements first to gather all UV/normal data
                primitives = mesh.findall('triangles') + mesh.findall('polylist')
                all_uv_sources = {uv_id} if uv_id else set()
                all_norm_sources = {norm_id} if norm_id else set()
                
                for prim in primitives:
                    prim_inputs = prim.findall('input')
                    for inp in prim_inputs:
                        sem = inp.attrib['semantic']
                        if sem not in ('VERTEX',):
                            src = inp.attrib.get('source', '').lstrip('#')
                            if sem == 'TEXCOORD':
                                all_uv_sources.add(src)
                            elif sem == 'NORMAL':
                                all_norm_sources.add(src)

                # Write vertex data
                obj_f.write(f"o Mesh_{mesh_idx}\n")
                for v in pos_data:
                    obj_f.write('v %.4f %.4f %.4f\n' % tuple(v[:3]))
                for vn in norm_data:
                    obj_f.write('vn %.4f %.4f %.4f\n' % tuple(vn[:3]))
                for uv in uv_data:
                    u, v = uv[0], uv[1]
                    obj_f.write('vt %.4f %.4f\n' % (u, v))  # Keep original V coordinate
                
                # Write all additional UV sources from primitives
                prim_uv_offsets = {uv_id: global_vt_offset}
                prim_norm_offsets = {norm_id: global_vn_offset}
                current_vt_offset = global_vt_offset + len(uv_data)
                current_vn_offset = global_vn_offset + len(norm_data)
                
                for uv_src in all_uv_sources:
                    if uv_src and uv_src not in prim_uv_offsets:
                        uv_src_data = sources.get(uv_src, [])
                        prim_uv_offsets[uv_src] = current_vt_offset
                        for uv in uv_src_data:
                            u, v = uv[0], uv[1]
                            obj_f.write('vt %.4f %.4f\n' % (u, v))
                        current_vt_offset += len(uv_src_data)
                
                for norm_src in all_norm_sources:
                    if norm_src and norm_src not in prim_norm_offsets:
                        norm_src_data = sources.get(norm_src, [])
                        prim_norm_offsets[norm_src] = current_vn_offset
                        for vn in norm_src_data:
                            obj_f.write('vn %.4f %.4f %.4f\n' % tuple(vn[:3]))
                        current_vn_offset += len(norm_src_data)

                has_uv = len(uv_data) > 0
                has_vn = len(norm_data) > 0

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
                    prim_uv_src = prim_src.get('TEXCOORD', uv_id if unified else None)
                    prim_norm_src = prim_src.get('NORMAL', norm_id if unified else None)
                    
                    prim_uv_data   = sources.get(prim_uv_src, []) if prim_uv_src else []
                    prim_norm_data = sources.get(prim_norm_src, []) if prim_norm_src else []
                    prim_has_uv = len(prim_uv_data) > 0
                    prim_has_vn = len(prim_norm_data) > 0
                    
                    # Get the correct offset for this source
                    prim_uv_offset = prim_uv_offsets.get(prim_uv_src, global_vt_offset)
                    prim_norm_offset = prim_norm_offsets.get(prim_norm_src, global_vn_offset)

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
                                    vt_idx = v_raw + 1 + prim_uv_offset if prim_has_uv else None
                                    vn_idx = v_raw + 1 + prim_norm_offset if prim_has_vn else None
                                else:
                                    v_idx  = v_raw + 1 + global_v_offset
                                    vt_raw = chunk[prim_offset['TEXCOORD']] if 'TEXCOORD' in prim_offset else None
                                    vn_raw = chunk[prim_offset['NORMAL']]   if 'NORMAL'   in prim_offset else None
                                    vt_idx = (vt_raw + 1 + prim_uv_offset) if vt_raw is not None else None
                                    vn_idx = (vn_raw + 1 + prim_norm_offset) if vn_raw is not None else None

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
                global_vt_offset = current_vt_offset
                global_vn_offset = current_vn_offset

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
                mtl_f.write("Ks 0.5 0.5 0.5\n")  # Slightly reflective
                mtl_f.write("Ns 32.0\n")  # Shininess
                mtl_f.write("d 1.0\n")
                mtl_f.write("illum 2\n")  # Highlight on
                if tex_file:
                    mtl_f.write(f"map_Kd {tex_file}\n")
                    mtl_f.write(f"map_bump {tex_file}\n")  # Use same for bump
                mtl_f.write("\n")
            if not written:
                # Fallback single material
                mtl_f.write("newmtl Material_001\n")
                mtl_f.write("Ka 1.0 1.0 1.0\nKd 1.0 1.0 1.0\n")

        return True, "Conversion successful!", all_textures

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"Error: {str(e)}", set()


def _apply_textures_to_materials(mat_tex_map, texture_dir, input_dir=''):
    """
    Post-process imported materials to link textures to shader nodes.
    Applies only textures explicitly defined in the DAE material map.
    Searches for textures with priority: input_dir (DAE location) first, then texture_dir.
    """
    print(f"\n=== _apply_textures_to_materials DEBUG ===")
    print(f"Material->Texture map: {mat_tex_map}")
    print(f"Texture dir: {texture_dir}")
    print(f"Input dir: {input_dir}")
    
    imported_objects = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    print(f"Selected mesh objects: {[obj.name for obj in imported_objects]}")
    
    for obj in imported_objects:
        print(f"\n--- Processing object: {obj.name} ---")
        if not obj.data.materials:
            print(f"  No material slots found")
            continue
        
        print(f"  Material slots: {[slot.name for slot in obj.material_slots]}")
        
        # Process each material slot in the imported object
        for mat_slot in obj.material_slots:
            mat = mat_slot.material
            if mat is None:
                print(f"  Slot has no material")
                continue
            
            print(f"\nProcessing material: '{mat.name}'")
            
            # Find texture for this material from DAE map
            tex_file = None
            matched_mat_id = None
            
            print(f"  Checking matches against {len(mat_tex_map)} DAE materials:")
            for mat_id, (mat_name, tex) in mat_tex_map.items():
                print(f"    - DAE: id='{mat_id}', name='{mat_name}', texture='{tex}'")
            
            # Try 1: Exact name match
            for mat_id, (mat_name, tex) in mat_tex_map.items():
                if mat_name == mat.name and tex:
                    tex_file = tex
                    matched_mat_id = mat_id
                    print(f"  ✓ Matched (exact): DAE material '{mat_name}' -> texture '{tex}'")
                    break
            
            # Try 2: Fuzzy match (contains) - for cases like "phong1" vs similar names
            if not tex_file:
                for mat_id, (mat_name, tex) in mat_tex_map.items():
                    if mat_name and mat_name in mat.name and tex:
                        tex_file = tex
                        matched_mat_id = mat_id
                        print(f"  ✓ Matched (fuzzy): DAE material '{mat_name}' in '{mat.name}' -> texture '{tex}'")
                        break
            
            # Try 3: For generic names like "phong1", "phong2", etc., try ordered matching
            if not tex_file and mat_tex_map:
                # For Spikanor-style materials (phong1, phong2, etc.)
                # Use the first available texture if no direct match
                for mat_id, (mat_name, tex) in mat_tex_map.items():
                    if tex and not tex_file:
                        tex_file = tex
                        matched_mat_id = mat_id
                        print(f"  ✓ Matched (fallback): DAE material '{mat_name}' -> texture '{tex}'")
                        break
            
            if not tex_file:
                # No texture defined for this material in DAE
                print(f"  ✗ No texture mapping found for '{mat.name}' in DAE")
                continue
            
            # Search for texture file with priority
            tex_path = None
            tex_basename = os.path.basename(tex_file)
            
            print(f"  Searching for texture: '{tex_basename}'")
            print(f"    Input dir: {input_dir}")
            print(f"    Texture dir: {texture_dir}")
            
            # Priority 1: Search in input directory (DAE location) and its subdirectories
            if input_dir and os.path.isdir(input_dir):
                # First check the directory itself
                candidate = os.path.join(input_dir, tex_basename)
                if os.path.exists(candidate):
                    tex_path = candidate
                    print(f"    ✓ Found in DAE directory: {candidate}")
                
                # Then search subdirectories
                if not tex_path:
                    print(f"    Searching subdirectories of {input_dir}...")
                    for root, dirs, files in os.walk(input_dir):
                        if tex_basename in files:
                            tex_path = os.path.join(root, tex_basename)
                            print(f"    ✓ Found in subdirectory: {tex_path}")
                            break
            else:
                print(f"    Input dir is not a valid directory")
            
            # Priority 2: Search in texture directory (OBJ output location)
            if not tex_path and texture_dir and os.path.isdir(texture_dir):
                print(f"    Searching texture directory {texture_dir}...")
                candidate = os.path.join(texture_dir, tex_basename)
                if os.path.exists(candidate):
                    tex_path = candidate
                    print(f"    ✓ Found in output directory: {candidate}")
            
            # If still not found, try exact path
            if not tex_path and os.path.exists(tex_file):
                tex_path = tex_file
                print(f"    ✓ Found as exact path: {tex_file}")
            
            if not tex_path:
                print(f"    ✗ MISSING: Could not find texture '{tex_basename}' for material '{mat.name}'")
                print(f"    Files in {input_dir}: {os.listdir(input_dir) if os.path.isdir(input_dir) else 'N/A'}")
                continue
            
            # Normalize path for Blender (use absolute path)
            tex_path = os.path.abspath(tex_path)
            
            # Apply shader nodes and texture
            try:
                if not os.path.exists(tex_path):
                    print(f"    ✗ ERROR: Texture file does not exist at: {tex_path}")
                    continue
                
                mat.use_nodes = True
                mat.node_tree.nodes.clear()
                links = mat.node_tree.links
                
                # Create shader nodes
                output_node = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
                principled = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                image_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
                
                # Load texture image - ensure Blender can find it
                print(f"    Loading image: {tex_path}")
                image = bpy.data.images.load(tex_path, check_existing=True)
                image_node.image = image
                print(f"    Image loaded: {image.name}")
                
                # Connect color
                links.new(image_node.outputs['Color'], principled.inputs['Base Color'])
                
                # Connect alpha - ALWAYS, not conditionally
                try:
                    links.new(image_node.outputs['Alpha'], principled.inputs['Alpha'])
                    print(f"    ✓ Alpha channel connected")
                except:
                    print(f"    Note: Could not connect alpha channel")
                
                links.new(principled.outputs['BSDF'], output_node.inputs['Surface'])
                
                # Enable alpha blending in material
                mat.shadow_method = 'NONE'  # No shadow for transparent materials
                mat.blend_method = 'BLEND'  # Use alpha blending
                
                print(f"    ✓ Applied texture to '{mat.name}': {tex_basename}")
                
            except Exception as e:
                print(f"    ✗ ERROR applying texture to '{mat.name}': {str(e)}")
                import traceback
                traceback.print_exc()
        
        # Separate by material
        try:
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.mesh.separate(type='MATERIAL')
        except:
            pass
    
    print(f"=== End _apply_textures_to_materials ===\n")


def _detect_import_number():
    """Detect which import number this is (1st, 2nd, 3rd, etc) by checking existing Mesh_N objects."""
    import_count = 0
    
    # Count how many Mesh_N base groups exist (N=0,1,2,3...)
    # Mesh_0.001, Mesh_0.002, ... = import 0
    # Mesh_1.001, Mesh_1.002, ... = import 1
    # Mesh_2.001, Mesh_2.002, ... = import 2
    
    mesh_bases = set()
    for obj in bpy.data.objects:
        # Match pattern: Mesh_N.NNN
        if obj.name.startswith("Mesh_") and "." in obj.name:
            parts = obj.name.split(".")
            if parts[0].startswith("Mesh_") and len(parts) >= 2 and parts[1].isdigit():
                try:
                    base_num = int(parts[0].split("_")[1])
                    mesh_bases.add(base_num)
                except:
                    pass
    
    # Next import number is the max found + 1
    if mesh_bases:
        import_count = max(mesh_bases) + 1
    
    return import_count


def _rename_duplicates():
    """Rename imported meshes with Mesh_N.001, Mesh_N.002, etc pattern and textures accordingly."""
    imported_objects = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    
    if not imported_objects:
        return
    
    # Detect which import number this is
    import_number = _detect_import_number()
    print(f"Detected import number: {import_number}")
    
    print(f"Renaming meshes to Mesh_{import_number}.NNN pattern")
    
    # Track mesh index within this import
    mesh_index = 1
    
    for obj in imported_objects:
        base_name = obj.name
        
        # Rename mesh to: Mesh_{import_number}.{mesh_index:03d}
        new_name = f"Mesh_{import_number}.{mesh_index:03d}"
        if new_name not in bpy.data.objects:
            obj.name = new_name
            print(f"Renamed mesh: {base_name} -> {new_name}")
        
        # Process materials and textures
        for mat_slot in obj.material_slots:
            if mat_slot.material:
                mat = mat_slot.material
                mat_base_name = mat.name
                
                # Rename material to match mesh
                mat_new_name = f"Material_{import_number}.{mesh_index:03d}"
                if mat_new_name not in bpy.data.materials:
                    mat.name = mat_new_name
                    print(f"Renamed material: {mat_base_name} -> {mat_new_name}")
                
                # Copy and rename texture files
                if mat.use_nodes:
                    for node in mat.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            old_image = node.image
                            old_filepath = old_image.filepath
                            
                            if old_filepath and os.path.exists(old_filepath):
                                # Create new texture filename: texture_{import_number}.{mesh_index:03d}.png
                                tex_dir = os.path.dirname(old_filepath)
                                tex_name = os.path.basename(old_filepath)
                                name_parts = os.path.splitext(tex_name)
                                
                                new_tex_name = f"texture_{import_number}.{mesh_index:03d}{name_parts[1]}"
                                new_filepath = os.path.join(tex_dir, new_tex_name)
                                
                                # Copy texture file if it doesn't exist
                                if not os.path.exists(new_filepath):
                                    try:
                                        shutil.copy(old_filepath, new_filepath)
                                        print(f"Copied texture: {tex_name} -> {new_tex_name}")
                                        
                                        # Load new image and update node
                                        new_image = bpy.data.images.load(new_filepath, check_existing=True)
                                        node.image = new_image
                                        print(f"Updated node to use: {new_tex_name}")
                                    except Exception as e:
                                        print(f"Could not copy texture: {str(e)}")
                                else:
                                    # File already exists, just load and update node
                                    try:
                                        new_image = bpy.data.images.load(new_filepath, check_existing=True)
                                        node.image = new_image
                                        print(f"Updated node to use existing: {new_tex_name}")
                                    except Exception as e:
                                        print(f"Could not load texture: {str(e)}")
        
        mesh_index += 1


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
        
        # Parse material-texture mapping
        mat_tex_map = {}
        try:
            tree = ET.ElementTree(file=input_path)
            for el in tree.iter():
                if '}' in el.tag:
                    el.tag = el.tag.split('}', 1)[1]
            mat_tex_map, _ = _build_material_texture_map(tree, input_dir)
            print(f"DEBUG: Extracted material->texture map: {mat_tex_map}")
        except Exception as e:
            print(f"ERROR: Could not extract material map: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # Copy ALL textures to the same folder as the output OBJ file
        output_dir = os.path.dirname(output_path)
        copied_textures = []
        dae_dir = os.path.dirname(input_path)  # DAE file's own directory
        
        for tex_name in all_textures:
            src_tex = None
            
            # Priority 1: Look in the DAE file's own directory
            candidate = os.path.join(dae_dir, os.path.basename(tex_name))
            if os.path.exists(candidate):
                src_tex = candidate
            
            # Priority 2: Search in subdirectories of DAE location
            if not src_tex:
                for root, dirs, files in os.walk(dae_dir):
                    if os.path.basename(tex_name) in files:
                        src_tex = os.path.join(root, os.path.basename(tex_name))
                        break
            
            # Priority 3: Search in parent input directory
            if not src_tex and input_dir != dae_dir:
                for root, dirs, files in os.walk(input_dir):
                    if os.path.basename(tex_name) in files:
                        src_tex = os.path.join(root, os.path.basename(tex_name))
                        break
            
            if src_tex and os.path.exists(src_tex):
                try:
                    # Copy to output directory (same as OBJ)
                    dst_tex = os.path.join(output_dir, os.path.basename(tex_name))
                    shutil.copy(src_tex, dst_tex)
                    copied_textures.append(dst_tex)
                except Exception as e:
                    self.report({'WARNING'}, f"Could not copy texture {tex_name}: {str(e)}")
        
        # Import the OBJ file using Blender's native importer
        try:
            if bpy.app.version >= (4, 0, 0):
                bpy.ops.wm.obj_import(filepath=output_path)
            else:
                bpy.ops.import_scene.obj(filepath=output_path)
            
            # Link textures to materials and separate by material
            self.report({'INFO'}, "Applying textures to materials...")
            if mat_tex_map and all_textures:
                output_dir = os.path.dirname(output_path)
                _apply_textures_to_materials(mat_tex_map, output_dir, input_dir)
            
            # Rename duplicates with .001, .002, etc
            self.report({'INFO'}, "Renaming duplicate meshes...")
            _rename_duplicates()
            
            # Clean up temporary OBJ/MTL files but KEEP textures (they are used by Blender)
            if self.use_temp_file:
                try:
                    import time
                    time.sleep(0.5)  # Give Blender time to load everything
                    # Don't delete - keep temp files so textures remain accessible
                    # OBJ/MTL were only intermediate conversion files
                except:
                    pass
            
            self.report({'INFO'}, f"Successfully imported with textures: {os.path.basename(input_path)}")
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