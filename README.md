# Blender COLLADA Importer via OBJ

A Blender addon that provides an alternative COLLADA (`.dae`) import pipeline by first converting to the Wavefront OBJ format, which resolves common texture and geometry issues with Blender's built-in COLLADA importer.

> Vibe-coded with [Claude.ai](https://claude.ai), based on the original design by [georgethrax/collada2obj](https://github.com/georgethrax/collada2obj).

---

## Why Does This Exist?

Blender's native COLLADA importer can sometimes struggle with certain `.dae` files — particularly assets exported from game engines or third-party tools — producing missing textures, broken normals, or geometry errors. This addon sidesteps those issues by parsing the COLLADA XML directly and converting it to OBJ + MTL files before feeding them into Blender's much more reliable OBJ importer.

---

## Files

| File | Description |
|------|-------------|
| `dea2obj2import.py` | **Main addon** — imports `.dae` files into Blender via an OBJ intermediate step, with full texture support, automatic texture copying, and intelligent mesh/material naming. |
| `dea2objconverter.py` | **Standalone converter addon** — adds a sidebar panel in the 3D Viewport to convert `.dae` files to OBJ+MTL on disk with material and texture mapping (without importing into Blender). Useful for batch conversion or external workflows. |

### Code Sharing & Synchronization

Both addons share the same **core conversion functions**:
- `_find_texture_file()` — recursive texture discovery
- `_parse_sources()` — extract vertex data from DAE
- `_parse_vertex_semantics()` — extract semantic mappings
- `_build_material_texture_map()` — extract material-texture relationships via COLLADA effect chain
- `convert_dae_to_obj()` — main DAE→OBJ conversion logic
- `_triangulate_face()` — polygon triangulation

**Important:** If you fix a bug or add a feature to any of these shared functions, please apply the same fix to **both** `dea2obj2import.py` and `dea2objconverter.py`. Each file has a comment block at the top of its conversion section documenting the shared functions.

---

## Features

- Parses COLLADA XML directly (no external dependencies)
- **Namespace-aware XML parsing** — supports both COLLADA 1.4.1 and 1.5.0 formats
- **Robust element discovery** — uses both namespace-aware queries and fallback mechanisms for maximum compatibility
- Extracts vertices, normals, and UV coordinates with proper offset tracking
- Detects and links texture images from `library_images` with recursive subdirectory search
- Automatic **material-to-texture mapping** via the effect chain (image → effect → material)
- Generates a proper `.mtl` material file alongside the OBJ with texture references
- Supports multiple mesh objects within a single `.dae` file (mesh separation by material)
- **Intelligent naming scheme** for multiple imports: `Mesh_N.NNN` pattern (e.g., first import: Mesh_0.001, Mesh_0.002; second import: Mesh_1.001, Mesh_1.002)
- Automatic texture file copying and renaming to match the naming scheme
- Handles both unified and separate vertex format types
- Handles Blender 2.80+ and Blender 4.x (uses the correct OBJ import operator for each version)
- Optional temp-file mode: converts to a temporary directory and cleans up after import
- Integrates cleanly into **File > Import > COLLADA (.dae)** menu

---

## Requirements

- Blender **2.80** or newer (including Blender 4.x)
- No external Python packages required — uses only Python's standard `xml.etree.ElementTree`

---

## Installation

### Option 1 — Install via Blender Preferences (recommended)

1. Download or clone this repository.
2. Open Blender and go to **Edit > Preferences > Add-ons**.
3. Click **Install...** and select either `dea2obj2import.py` or `dea2objconverter.py`.
4. Enable the addon by checking the checkbox next to its name.

### Option 2 — Manual installation

Copy the script(s) to your Blender addons directory:

- **Windows:** `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\`
- **macOS:** `~/Library/Application Support/Blender/<version>/scripts/addons/`
- **Linux:** `~/.config/blender/<version>/scripts/addons/`

Then enable the addon in **Edit > Preferences > Add-ons**.

---

## Usage

### Importing a COLLADA file (`dea2obj2import.py`)

1. Go to **File > Import > COLLADA Custom (.dae)**.
2. Browse to and select your `.dae` file.
3. Optionally toggle **Use Temporary File** in the import options panel:
   - **Enabled (default):** The intermediate OBJ and MTL are written to a system temp directory and deleted after import.
   - **Disabled:** The converted `.obj` and `.mtl` files are saved in the same directory as the source `.dae`, with the suffix `_converted`.
4. Click **Import COLLADA (.dae)**.

Make sure any texture files referenced by the `.dae` are located in the **same directory** as the `.dae` file. The addon will find and copy them automatically during import.

### Converting a file to OBJ (`dea2objconverter.py`)

1. Open the **3D Viewport** sidebar (press **N** if hidden).
2. Navigate to the **DAE Converter** tab.
3. Click **Convert .dae to .obj**.
4. Select your `.dae` file in the file browser.
5. The converted `.obj` and `.mtl` files will be saved in the **same directory** as the source file.
   - The `.obj` file will be named `<original_name>.obj`
   - The `.mtl` file will contain all extracted materials and texture references
   - Texture files are NOT copied; only geometric and material data are extracted

---

## How It Works

1. The `.dae` file is parsed as XML using Python's standard library.
2. **COLLADA namespace handling:**
   - The code uses namespace-aware XML queries (`{*}tag`) to work with namespaced COLLADA files
   - A fallback mechanism directly iterates through elements and matches by tag name for compatibility with different DAE formats
   - This dual approach ensures broad compatibility across various COLLADA versions (1.4.x and 1.5.x)
3. **Material-to-texture extraction** via the COLLADA effect chain:
   - Images are extracted from `library_images` with support for both direct (`<init_from>filename</init_from>`) and nested (`<init_from><ref>filename</ref></init_from>`) formats
   - Effects are extracted from `library_effects`, connecting to images via surface references
   - Materials are extracted from `library_materials`, linking to effects via `instance_effect`
   - This chain (image → effect → material) is traversed to build the complete material-texture map
   - Texture files are searched recursively in subdirectories (e.g., "Custom Color 1/", "Alt Textures/")
4. Geometry data is extracted from `library_geometries`:
   - Vertex positions from the `VERTEX` source
   - Normals from the `NORMAL` source
   - UV coordinates from the `TEXCOORD` source
5. The data is written to a temporary (or permanent) `.obj` file with a matching `.mtl` material file containing all discovered textures.
6. Blender's native OBJ importer (`wm.obj_import` on Blender 4+ or `import_scene.obj` on older versions) handles the final import.
7. **Mesh and material renaming:**
   - All imported meshes are renamed to the pattern `Mesh_N.NNN` where N is the import counter
   - Materials are renamed to `Material_N.NNN` to match
   - Texture files are copied and renamed accordingly
   - If a file with the same name already exists, it's preserved and reused (avoiding unnecessary duplication)

### Multi-mesh support

When a `.dae` file contains multiple `<geometry>` elements, each is parsed into a separate mesh object (`Mesh_0`, `Mesh_1`, etc.) and written as named objects (`o`) in the OBJ file. Vertex, UV, and normal index offsets are tracked and adjusted correctly for each mesh.

---

## Known Limitations

- Only triangulated faces are supported (`<triangles>` elements). `<polylist>` elements are converted to triangles via fan-triangulation, while `<polygons>` elements are not currently handled.
- COLLADA features such as animations, skinning, cameras, and lights are ignored — only static geometry is imported.
- The `dea2objconverter.py` script does not copy texture files; it only outputs geometry. Use `dea2obj2import.py` for complete texture support.

## Recent Improvements (v1.1)

- ✅ **Full namespace support** for namespaced COLLADA format files
- ✅ **Material-to-texture mapping** via COLLADA effect chain (image → effect → material)
- ✅ **Recursive texture discovery** in subdirectories
- ✅ **Multiple texture format support** (both `<init_from>filename</init_from>` and `<init_from><ref>filename</ref></init_from>`)
- ✅ **Mesh separation by material** with proper naming scheme
- ✅ **Automatic texture copying and renaming** for multi-import workflows
- ✅ **UV map support** with proper offset tracking for both unified and separate vertex formats

---

## Contributing

Contributions and bug reports are welcome. If you encounter a `.dae` file that doesn't import correctly, feel free to open an issue with details about the file format and any error messages.

---

## License

See [LICENSE](LICENSE) for details.

---

## Credits

- Original COLLADA-to-OBJ conversion concept: [georgethrax/collada2obj](https://github.com/georgethrax/collada2obj)
- Addon development: vibe-coded with [Claude.ai](https://claude.ai)