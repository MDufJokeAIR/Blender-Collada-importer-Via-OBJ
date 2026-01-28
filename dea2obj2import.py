#coding:utf-8
"""
DAE to OBJ Importer - Blender Addon
Imports COLLADA (.dae) files by converting to OBJ then using Blender's native OBJ importer
"""

bl_info = {
    "name": "Import COLLADA via OBJ Converter",
    "author": "Your Name",
    "version": (1, 0, 0),
    "blender": (2, 80, 0),
    "location": "File > Import > COLLADA (.dae)",
    "description": "Import COLLADA (.dae) files by converting to OBJ format",
    "category": "Import-Export",
}

import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper
from bpy.types import Operator
import os
import tempfile

try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


# ============================================================================
# Model Class (from original script)
# ============================================================================

class Model(object):
    def __init__(self, residues=[], args=None, style='strand'):
        self.v = []   # vertices
        self.vn = []  # normals
        self.vt = []  # texture coords
        self.f = []   # faces
    
    def set_f(self, f_list):
        self.f = f_list
    
    def exportObj(self, obj3d_path=''):
        obj_str = []
        obj_str = obj_str + list(map(lambda v: 'v %.3f %.3f %.3f\n' % tuple(v), self.v))
        obj_str = obj_str + list(map(lambda v: 'vn %.3f %.3f %.3f\n' % tuple(v), self.vn))
        obj_str = obj_str + list(map(lambda v: 'vt %.3f %.3f %.3f\n' % tuple(v), self.vt))
        
        if self.f:
            if len(self.f[0]) == 3:
                f_str = list(map(lambda f: 'f ' + ' '.join(map(str, f)) + '\n', self.f))
            elif len(self.f[0]) == 6:
                f_str = list(map(lambda f: 'f %d//%d %d//%d %d//%d\n' % tuple(f), self.f))
            obj_str = obj_str + f_str
        
        self.obj_str = obj_str
        if obj3d_path:
            with open(obj3d_path, 'w') as f:
                f.writelines(obj_str)
        else:
            return obj_str
    
    @staticmethod
    def reduce(ma, mb):
        mc = Model()
        mc.v = (ma.v + mb.v)
        mc.vn = (ma.vn + mb.vn)
        mc.vt = (ma.vt + mb.vt)
        num_va = len(ma.v)
        num_vna = len(ma.vn)
        num_vta = len(ma.vt)
        f = mb.f
        
        if f:
            if len(f[0]) == 3:
                f = list(map(lambda fi: [x + num_va for x in fi], f))
            elif len(f[0]) == 6:
                for fi in f:
                    fi[::2] = [x + num_va for x in fi[::2]]
                    fi[1::2] = [x + num_vna for x in fi[1::2]]
            elif len(f[0]) == 9:
                for fi in f:
                    fi[::3] = [x + num_va for x in fi[::3]]
                    fi[1::3] = [x + num_vta for x in fi[1::3]]
                    fi[2::3] = [x + num_vna for x in fi[2::3]]
            mc.f = (ma.f + f)
        return mc


# ============================================================================
# Conversion Function
# ============================================================================

def convert_dae_to_obj(input_filepath, output_filepath):
    """Convert a DAE file to OBJ format"""
    try:
        xmlns = "{http://www.collada.org/2005/11/COLLADASchema}"
        
        # Parse XML
        tree = ET.ElementTree(file=input_filepath)
        
        # Fix xmlns problem
        for el in tree.iter():
            if '}' in el.tag:
                el.tag = el.tag.split('}', 1)[1]
        
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
            
            for triangles_input in triangles.findall('input'):
                triangles_offset_dict[triangles_input.attrib['semantic']] = int(triangles_input.attrib['offset'])
                if triangles_input.attrib['semantic'] == 'VERTEX':
                    source_id_dict['VERTEX'] = vertices.find('input').attrib['source'][1:]
                elif triangles_input.attrib['semantic'] == 'NORMAL':
                    source_id_dict['NORMAL'] = triangles_input.attrib['source'][1:]
            
            source_float_dict = {}
            for source in sources:
                float_array = list(map(float, source.find('float_array').text.split()))
                source_float_dict[source.attrib['id']] = list(map(lambda i: float_array[i:i+3], range(0, len(float_array), 3)))
            
            model = Model()
            model.v = source_float_dict[source_id_dict['VERTEX']]
            model.vn = source_float_dict[source_id_dict['NORMAL']]
            
            p_text_str = triangles_p.text.split()
            p_text = list(map(lambda idx: int(idx) + 1, p_text_str))
            obj_f = list(map(lambda i: p_text[i:i+len(triangles_offset_dict)*3], range(0, len(p_text), len(triangles_offset_dict)*3)))
            f_list = list(map(lambda f: [f[triangles_offset_dict['VERTEX']], f[triangles_offset_dict['NORMAL']], 
                                   f[triangles_offset_dict['VERTEX']+len(triangles_offset_dict)], 
                                   f[triangles_offset_dict['NORMAL']+len(triangles_offset_dict)], 
                                   f[triangles_offset_dict['VERTEX']+len(triangles_offset_dict)*2], 
                                   f[triangles_offset_dict['NORMAL']+len(triangles_offset_dict)*2]], obj_f))
            model.set_f(f_list)
            models.append(model)
        
        if models:
            from functools import reduce
            final_model = reduce(Model.reduce, models)
            final_model.exportObj(output_filepath)
            return True, "Conversion successful!"
        else:
            return False, "No meshes found in DAE file"
            
    except Exception as e:
        return False, f"Error during conversion: {str(e)}"


# ============================================================================
# Import Operator
# ============================================================================

class ImportDAE(Operator, ImportHelper):
    """Import COLLADA (.dae) file"""
    bl_idname = "import_scene.dae_via_obj"
    bl_label = "Import COLLADA (.dae)"
    bl_options = {'REGISTER', 'UNDO'}
    
    # ImportHelper properties
    filename_ext = ".dae"
    filter_glob: StringProperty(
        default="*.dae",
        options={'HIDDEN'}
    )
    
    # Additional options
    use_temp_file: BoolProperty(
        name="Use Temporary File",
        description="Convert to temporary OBJ file (recommended)",
        default=True
    )
    
    def execute(self, context):
        input_path = bpy.path.abspath(self.filepath)
        
        # Determine output path
        if self.use_temp_file:
            # Create temporary file
            temp_dir = tempfile.gettempdir()
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(temp_dir, base_name + "_temp.obj")
        else:
            # Use same directory as input file
            output_path = os.path.splitext(input_path)[0] + "_converted.obj"
        
        # Convert DAE to OBJ
        self.report({'INFO'}, "Converting DAE to OBJ...")
        success, message = convert_dae_to_obj(input_path, output_path)
        
        if not success:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        
        # Import the OBJ file using Blender's native importer
        try:
            self.report({'INFO'}, "Importing OBJ file...")
            bpy.ops.wm.obj_import(filepath=output_path)
            
            # Clean up temporary file if used
            if self.use_temp_file:
                try:
                    os.remove(output_path)
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
    self.layout.operator(ImportDAE.bl_idname, text="COLLADA (.dae)")


# ============================================================================
# Registration
# ============================================================================

classes = (
    ImportDAE,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # Add to File > Import menu
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    # Remove from File > Import menu
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()