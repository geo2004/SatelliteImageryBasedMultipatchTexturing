An ArcGIS Pro Python Toolbox (`.pyt`) for mass-texturing multipatch buildings with per-building satellite imagery from Esri World Imagery.

This tool bridges the gap between raw 3D building models (extracted via 3D Analyst) and realistic urban visualizations. It automatically downloads high-resolution satellite imagery for each building's footprint, injects it into a CityEngine Rule Package (.rpk), and maps the textures to both flat and sloped roof faces—all without requiring manual CGA rule editing or CGB compilation per run.

## Features
* **Native ArcGIS Pro UI:** Runs directly from the Geoprocessing pane. Accepts Map Layers or raw File Geodatabase paths.
* **Smart Attribute Mapping:** Automatically adds `Flat_Roof_Texture` and `Sloped_Roof_Texture` fields to your multipatch feature class.
* **Zero OID Conflicts:** Utilizes isolated temporary directories for satellite caching and `.rpk` staging. Cache is automatically wiped after execution, ensuring no texture mix-ups between different feature classes.

## Folder Structure

For the tool to function, your repository/folder must maintain this exact structure before running:

```text
SatelliteRoofTexturer/
├── SatelliteRoofTexturer.pyt           ← The ArcGIS Pro Geoprocessing Tool
├── SatelliteRoofTexturer.pyt.xml       ← Tool metadata and help documentation
├── README.md
└── BaseRPK/                            ← Unzipped template from CityEngine
    ├── .resolvemap.xml
    ├── esriinfo/
    ├── rules/
    │   ├── SatelliteRoof_Multipatch.cga
    │   └── SatelliteRoof_Multipatch.cgb 
    └── assets/
        └── 3D_City_Design_Assets/
            └── Material_Library/
                ├── FlatRoof/           ← Can be empty
                └── SlopedRoof/         ← Can be empty
```
## Usage
```text
1. Open ArcGIS Pro.
2. In the Catalog pane, right-click Toolboxes → Add Toolbox.
3. Browse to and select SatelliteRoofTexturer.pyt.
4. Open the Texture Roof Multipatch From Satellite Imagery tool.
5. Input Multipatch Feature Class: Select your untextured building/Multipatch layer (must be a Multipatch).
6. Output Rule Package (.rpk): Choose the save destination for your new textured RPK.
7. Click Run.
```
## After Running
To apply the generated textures to your scene:
1. Ensure your updated multipatch feature class is added to a Local or Global Scene.
2. Open the Symbology pane for the layer and change the primary symbology to Procedural Fill.
3. Load the output .rpk file you just generated to the Rules Button.
4. In the attribute mapping section, map the `Flat_Roof_Texture` rule parameter to the newly created `Flat_Roof_Texture` attribute field.
5. The satellite imagery will instantly render on all valid roof faces.

## Requirements
1. ArcGIS Pro (with 3D Analyst extension recommended for initial Multipatch extraction from LIDAR or DSM Data.).
2. Active internet connection (downloads Esri World Imagery tiles at zoom level 19).
3. Standard Python libraries included with ArcGIS Pro (`arcpy, requests, Pillow`).
