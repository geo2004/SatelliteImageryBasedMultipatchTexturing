An ArcGIS Pro Python Toolbox (`.pyt`) for mass-texturing LoD2 multipatch buildings with per-building satellite imagery from Esri World Imagery.

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
