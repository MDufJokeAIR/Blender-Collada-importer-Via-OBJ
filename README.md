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
| `dea2obj2import.py` | **Main addon** — imports `.dae` files into Blender via an OBJ intermediate step, with full texture and UV support. |
| `dea2objconverter.py` | **Standalone converter addon** — adds a sidebar panel in the 3D Viewport to convert `.dae` files to `.obj` on disk without directly importing them. |

---

## Features

- Parses COLLADA XML directly (no external dependencies)
- Extracts vertices, normals, and UV coordinates
- Detects and links texture images from `library_images`
- Generates a proper `.mtl` material file alongside the OBJ
- Supports multiple mesh objects within a single `.dae` file
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
5. The converted `.obj` file will be saved in the **same directory** as the source file.

---

## How It Works

1. The `.dae` file is parsed as XML using Python's standard library.
2. The COLLADA namespace is stripped from all tags for uniform access.
3. Geometry data is extracted from `library_geometries`:
   - Vertex positions from the `VERTEX` source
   - Normals from the `NORMAL` source
   - UV coordinates from the `TEXCOORD` source
4. The first image found in `library_images` is used as the diffuse texture.
5. The data is written to a temporary (or permanent) `.obj` file with a matching `.mtl` material file.
6. Blender's native OBJ importer (`wm.obj_import` on Blender 4+ or `import_scene.obj` on older versions) handles the final import.

### Multi-mesh support

When a `.dae` file contains multiple `<geometry>` elements, each is parsed into a separate mesh object (`Mesh_0`, `Mesh_1`, etc.) and written as named objects (`o`) in the OBJ file. Vertex, UV, and normal index offsets are tracked and adjusted correctly for each mesh.

---

## Known Limitations

- Only triangulated faces are supported (`<triangles>` elements). Polygonal faces (`<polylist>`, `<polygons>`) are not currently handled.
- Only the **first image** found in `library_images` is used for texturing. Files with multiple materials are not fully supported.
- COLLADA features such as animations, skinning, cameras, and lights are ignored — only static geometry is imported.
- The `dea2objconverter.py` script does not copy or resolve texture files; it only outputs geometry.

---

## Contributing

Contributions and bug reports are welcome. If you encounter a `.dae` file that doesn't import correctly, feel free to open an issue with a minimal reproducible example.

---

## License

See [LICENSE](LICENSE) for details.

---

## Credits

- Original COLLADA-to-OBJ conversion concept: [georgethrax/collada2obj](https://github.com/georgethrax/collada2obj)
- Addon development: vibe-coded with [Claude.ai](https://claude.ai)