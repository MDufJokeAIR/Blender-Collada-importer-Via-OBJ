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
# Model Class
# ============================================================================

class Model(object):
    def __init__(self):
        self.v = []   # vertices
        self.vn = []  # normals
        self.vt = []  # texture coords (UVs)
        self.f = []   # faces
        self.material_name = "Material_001"
        self.texture_file = None # Stores the texture filename (e.g., image.png)
    
    def set_f(self, f_list):
        self.f = f_list
    
    def create_mtl_file(self, mtl_path):
        """Generates a simple MTL file linking to the texture"""
        content = []
        content.append(f"newmtl {self.material_name}")
        content.append("Ka 1.000 1.000 1.000")
        content.append("Kd 1.000 1.000 1.000")
        content.append("Ks 0.000 0.000 0.000")
        content.append("d 1.0")
        content.append("illum 1")
        
        if self.texture_file:
            # map_Kd is the diffuse texture map
            content.append(f"map_Kd {self.texture_file}")
            
        with open(mtl_path, 'w') as f:
            f.write('\n'.join(content))

    def exportObj(self, obj3d_path=''):
        obj_str = []
        
        # Link to MTL file
        base_filename = os.path.basename(obj3d_path)
        mtl_filename = os.path.splitext(base_filename)[0] + ".mtl"
        obj_str.append(f"mtllib {mtl_filename}\n")
        
        obj_str = obj_str + list(map(lambda v: 'v %.4f %.4f %.4f\n' % tuple(v), self.v))
        obj_str = obj_str + list(map(lambda v: 'vn %.4f %.4f %.4f\n' % tuple(v), self.vn))
        # Export UVs (Flip V coordinate for Blender/OBJ standard if necessary, usually OBJ assumes 0-1 bottom-left)
        obj_str = obj_str + list(map(lambda v: 'vt %.4f %.4f\n' % (v[0], v[1]), self.vt))
        
        obj_str.append(f"usemtl {self.material_name}\n")
        obj_str.append("s off\n")

        if self.f:
            # Face format depends on what data we have
            # f v/vt/vn
            has_uv = len(self.vt) > 0
            has_vn = len(self.vn) > 0

            for face in self.f:
                # face is a flat list: [v, vn, vt, v, vn, vt, ...] depending on source
                # The logic in convert_dae needs to ensure the order in 'face' list matches parsing logic
                # We will construct the string dynamically based on the parsing result
                
                # Assuming the converter passed formatted indices blocks
                # We reconstruct the 'f' line based on availability
                line_parts = ["f"]
                
                # The generic logic below assumes the converter formatted the face list correctly
                # But to make it robust with the updated converter logic:
                if len(face) == 3: # Only V
                    line_parts.append(f"{face[0]} {face[1]} {face[2]}")
                else:
                    # Construct v/vt/vn strings
                    # The converter below returns [v_idx, vt_idx, vn_idx] triplets if all exist
                    stride = 3 if (has_uv and has_vn) else 2
                    
                    for i in range(0, len(face), stride):
                        v_idx = face[i]
                        vt_idx = face[i+1] if has_uv else ''
                        vn_idx = face[i+2] if (has_uv and has_vn) else (face[i+1] if has_vn else '')
                        
                        line_parts.append(f"{v_idx}/{vt_idx}/{vn_idx}")
                        
                obj_str.append(" ".join(line_parts) + "\n")
        
        # Write OBJ
        with open(obj3d_path, 'w') as f:
            f.writelines(obj_str)
            
        # Write MTL alongside
        mtl_path = os.path.join(os.path.dirname(obj3d_path), mtl_filename)
        self.create_mtl_file(mtl_path)

    @staticmethod
    def reduce(ma, mb):
        mc = Model()
        mc.v = (ma.v + mb.v)
        mc.vn = (ma.vn + mb.vn)
        mc.vt = (ma.vt + mb.vt)
        mc.texture_file = ma.texture_file or mb.texture_file # Keep texture if one has it
        
        num_va = len(ma.v)
        num_vna = len(ma.vn)
        num_vta = len(ma.vt)
        
        f = mb.f
        if f:
            # Offset indices for appended mesh
            # Complex logic simplified: assumes the structure coming from convert_dae matches
            # stride is detected based on list length vs vertex count usually, 
            # but let's assume standard triplets [v, vt, vn] for now from our parser
            
            # Note: This reduce function is complex to maintain for flexible formats.
            # Simplified for v/vt/vn structure:
            new_f = []
            for face in f:
                new_face = []
                # Assuming triplets [v, vt, vn]
                stride = 3
                if not ma.vt and not mb.vt: stride = 2 # v, vn
                
                for i in range(len(face)):
                    val = face[i]
                    mod = i % stride
                    if mod == 0: val += num_va # v
                    elif mod == 1: val += num_vta # vt (if exist)
                    elif mod == 2: val += num_vna # vn
                    new_face.append(val)
                new_f.append(new_face)
            mc.f = (ma.f + new_f)
            
        return mc


# ============================================================================
# Conversion Function
# ============================================================================

def convert_dae_to_obj(input_filepath, output_filepath):
    """Convert a DAE file to OBJ format extracting Textures/UVs"""
    try:
        # Parse XML
        tree = ET.ElementTree(file=input_filepath)
        
        # Fix xmlns problem
        for el in tree.iter():
            if '}' in el.tag:
                el.tag = el.tag.split('}', 1)[1]
        
        # 1. Try to find the texture image filename in library_images
        # This is a basic implementation picking the first image found
        texture_name = None
        images = tree.findall('library_images/image')
        if images:
            # Usually inside <init_from>
            init_from = images[0].find('init_from')
            if init_from is not None:
                # remove file:// prefix if present
                raw_path = init_from.text.strip()
                if raw_path.startswith('file://'):
                    raw_path = raw_path[7:]
                # Just get filename
                texture_name = os.path.basename(raw_path)
                
        # Parse COLLADA
        meshes = tree.findall('library_geometries/geometry/mesh')
        models = []
        
        for mesh in meshes:
            sources = mesh.findall('source')
            vertices = mesh.find('vertices')
            triangles = mesh.find('triangles')
            
            if triangles is None:
                continue
                
            triangles_p = triangles.find('p')
            if triangles_p is None:
                continue
                
            triangles_offset_dict = {}
            source_id_dict = {}
            
            # Identify input sources
            for triangles_input in triangles.findall('input'):
                sem = triangles_input.attrib['semantic']
                off = int(triangles_input.attrib['offset'])
                triangles_offset_dict[sem] = off
                
                if sem == 'VERTEX':
                    source_id_dict['VERTEX'] = vertices.find('input').attrib['source'][1:]
                elif sem == 'NORMAL':
                    source_id_dict['NORMAL'] = triangles_input.attrib['source'][1:]
                elif sem == 'TEXCOORD':
                    source_id_dict['TEXCOORD'] = triangles_input.attrib['source'][1:]
            
            # Read source arrays
            source_float_dict = {}
            for source in sources:
                float_array = source.find('float_array')
                if float_array is not None:
                    float_list = list(map(float, float_array.text.split()))
                    stride = int(source.find('technique_common/accessor').attrib['stride'])
                    
                    # Store vectors based on stride
                    source_float_dict[source.attrib['id']] = \
                        list(map(lambda i: float_list[i:i+stride], range(0, len(float_list), stride)))
            
            # Build Model
            model = Model()
            if texture_name:
                model.texture_file = texture_name
            
            # Handle Vertices
            if 'VERTEX' in source_id_dict:
                model.v = source_float_dict[source_id_dict['VERTEX']]
            
            # Handle Normals
            if 'NORMAL' in source_id_dict:
                model.vn = source_float_dict[source_id_dict['NORMAL']]
            
            # Handle Texture Coords (UVs)
            has_uv = False
            if 'TEXCOORD' in source_id_dict:
                model.vt = source_float_dict[source_id_dict['TEXCOORD']]
                # Ensure 2D UVs (some tools export 3D UVs with Z=0)
                model.vt = [uv[:2] for uv in model.vt]
                # Flip V coordinate for OBJ standard (optional depending on source)
                model.vt = [[u, 1.0 - v] for u, v in model.vt]
                has_uv = True
            
            # Parse Faces (p element)
            p_text_str = triangles_p.text.split()
            p_indices = list(map(int, p_text_str))
            
            # Calculate total stride per vertex definition in 'p'
            p_stride = len(triangles_offset_dict) 
            # Total values per triangle = 3 vertices * p_stride
            tri_step = p_stride * 3
            
            # Build Face List formatted for OBJ (1-based index)
            # We want to store triplets [v_idx, vt_idx, vn_idx] per vertex
            f_list = []
            
            num_indices = len(p_indices)
            for i in range(0, num_indices, tri_step):
                # Get the chunk for one triangle
                tri_chunk = p_indices[i : i+tri_step]
                
                # Extract indices for each vertex in the triangle
                v1_data = tri_chunk[0:p_stride]
                v2_data = tri_chunk[p_stride:p_stride*2]
                v3_data = tri_chunk[p_stride*2:p_stride*3]
                
                # Function to extract specific semantic index from vertex data chunk
                def get_idx(v_data, semantic):
                    if semantic in triangles_offset_dict:
                        offset = triangles_offset_dict[semantic]
                        return v_data[offset] + 1 # OBJ is 1-based
                    return None

                # Build OBJ face string components: "v/vt/vn"
                face_indices = []
                for v_dat in [v1_data, v2_data, v3_data]:
                    v_idx = get_idx(v_dat, 'VERTEX')
                    vn_idx = get_idx(v_dat, 'NORMAL')
                    vt_idx = get_idx(v_dat, 'TEXCOORD')
                    
                    face_str = f"{v_idx}"
                    
                    if has_uv:
                        face_str += f"/{vt_idx}" if vt_idx else "/"
                    else:
                        # If no UVs globally but we have normals, use double slash
                        if vn_idx: face_str += "//"
                            
                    if vn_idx:
                        face_str += f"/{vn_idx}" if has_uv else f"{vn_idx}"
                    
                    face_indices.append(face_str)
                
                # Store pre-formatted string to simplify export logic
                f_list.append(f"f {' '.join(face_indices)}\n")

            # Override export logic: store strings directly since we formatted them here
            model.obj_str_faces = f_list
            models.append(model)
        
        if models:
            # Simple export logic for single model for now (merging disabled for clarity/correctness)
            # If there are multiple meshes, we write them sequentially
            
            with open(output_filepath, 'w') as f:
                # Header
                base_mtl = os.path.splitext(os.path.basename(output_filepath))[0] + ".mtl"
                f.write(f"mtllib {base_mtl}\n")
                
                # Combined counters for merging multiple meshes into one OBJ file
                v_offset = 0
                vt_offset = 0
                vn_offset = 0
                
                for idx, m in enumerate(models):
                    f.write(f"o Mesh_{idx}\n")
                    
                    # Write Data
                    for v in m.v: f.write('v %.4f %.4f %.4f\n' % tuple(v))
                    for vn in m.vn: f.write('vn %.4f %.4f %.4f\n' % tuple(vn))
                    for vt in m.vt: f.write('vt %.4f %.4f\n' % (vt[0], vt[1]))
                    
                    f.write(f"usemtl {m.material_name}\n")
                    f.write("s off\n")
                    
                    # Write Faces (need to offset indices)
                    for face_line in m.obj_str_faces:
                        # Parse the pre-built string back to adjust indices
                        # Format: "f v/vt/vn v/vt/vn v/vt/vn"
                        parts = face_line.strip().split()
                        new_parts = ["f"]
                        for p in parts[1:]:
                            sub = p.split('/')
                            # v
                            v_idx = int(sub[0]) + v_offset
                            res = f"{v_idx}"
                            
                            if len(sub) > 1:
                                # vt
                                if sub[1]:
                                    vt_idx = int(sub[1]) + vt_offset
                                    res += f"/{vt_idx}"
                                else:
                                    res += "/"
                                
                                # vn
                                if len(sub) > 2:
                                    vn_idx = int(sub[2]) + vn_offset
                                    res += f"/{vn_idx}"
                            
                            new_parts.append(res)
                        f.write(" ".join(new_parts) + "\n")
                        
                    # Update offsets
                    v_offset += len(m.v)
                    vt_offset += len(m.vt)
                    vn_offset += len(m.vn)
                
                # Generate MTL
                # We use the texture from the first model usually found
                main_tex = models[0].texture_file if models else None
                
                mtl_path = os.path.splitext(output_filepath)[0] + ".mtl"
                with open(mtl_path, 'w') as mtl:
                    mtl.write("newmtl Material_001\n")
                    mtl.write("Ka 1.0 1.0 1.0\n")
                    mtl.write("Kd 1.0 1.0 1.0\n")
                    if main_tex:
                        mtl.write(f"map_Kd {main_tex}\n")
            
            return True, "Conversion successful with textures!", models[0].texture_file
        else:
            return False, "No meshes found in DAE file", None
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"Error: {str(e)}", None


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
        success, message, texture_name = convert_dae_to_obj(input_path, output_path)
        
        if not success:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        
        # Copy texture to temp folder if needed so OBJ importer finds it
        if self.use_temp_file and texture_name:
            src_tex = os.path.join(input_dir, texture_name)
            dst_tex = os.path.join(tempfile.gettempdir(), texture_name)
            if os.path.exists(src_tex):
                try:
                    shutil.copy(src_tex, dst_tex)
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
                    if texture_name and os.path.exists(dst_tex):
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