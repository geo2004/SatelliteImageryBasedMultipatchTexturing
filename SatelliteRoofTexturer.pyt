# -*- coding: utf-8 -*-
import arcpy
import os
import io
import math
import re
import zipfile
import shutil
import tempfile
import requests
from PIL import Image

# ============================================================
# TILE MATH & HELPER FUNCTIONS
# ============================================================
def deg2tile(lat_deg, lon_deg, zoom):
    lat_r = math.radians(lat_deg)
    n = 2 ** zoom
    x = int((lon_deg + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return x, y

def tile2deg(x, y, zoom):
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat_r = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_r)
    return lat, lon

def get_wgs84_extent(shape):
    sr_wgs84 = arcpy.SpatialReference(4326)
    projected = shape.projectAs(sr_wgs84)
    ext = projected.extent
    return ext.XMin, ext.YMin, ext.XMax, ext.YMax

def download_tile(x, y, z, session, tile_url, retries=3):
    url = tile_url.format(z=z, x=x, y=y)
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
        except Exception as e:
            if attempt == retries - 1:
                arcpy.AddWarning(f"Tile {z}/{y}/{x} failed: {e}")
    return None

def fetch_roof_image(min_lon, min_lat, max_lon, max_lat, zoom, session, tile_url, output_size):
    x_min, y_min = deg2tile(max_lat, min_lon, zoom)
    x_max, y_max = deg2tile(min_lat, max_lon, zoom)

    tile_px = 256
    cols = x_max - x_min + 1
    rows = y_max - y_min + 1

    canvas = Image.new("RGB", (cols * tile_px, rows * tile_px))

    for ty in range(y_min, y_max + 1):
        for tx in range(x_min, x_max + 1):
            tile_img = download_tile(tx, ty, zoom, session, tile_url)
            if tile_img:
                canvas.paste(tile_img, ((tx - x_min) * tile_px, (ty - y_min) * tile_px))

    canvas_lat_max, canvas_lon_min = tile2deg(x_min,     y_min,     zoom)
    canvas_lat_min, canvas_lon_max = tile2deg(x_max + 1, y_max + 1, zoom)

    cw = cols * tile_px
    ch = rows * tile_px
    lon_span = canvas_lon_max - canvas_lon_min
    lat_span = canvas_lat_max - canvas_lat_min

    def clamp(v, lo, hi): return max(lo, min(v, hi))

    px_l = clamp(int((min_lon - canvas_lon_min) / lon_span * cw), 0, cw - 1)
    px_r = clamp(int((max_lon - canvas_lon_min) / lon_span * cw), 1, cw)
    px_t = clamp(int((canvas_lat_max - max_lat) / lat_span * ch), 0, ch - 1)
    px_b = clamp(int((canvas_lat_max - min_lat) / lat_span * ch), 1, ch)

    cropped = canvas.crop((px_l, px_t, px_r, px_b))
    return cropped.resize(output_size, Image.LANCZOS)


# ============================================================
# TOOLBOX CLASSES
# ============================================================
class Toolbox(object):
    def __init__(self):
        self.label = "Satellite Roof Texturer Toolbox"
        self.alias = "SatelliteRoofTexturer"
        self.tools = [SatelliteRoofTexturerTool]

class SatelliteRoofTexturerTool(object):
    def __init__(self):
        self.label = "Texture Building Model Roofs Using Raster"
        self.description = "Mass-textures multipatch building roofs using online tiles or local rasters."
        self.canRunInBackground = False

        # Configuration Constants
        self.zoom = 19
        self.tile_url = ""
        self.output_size = (512, 512)
        self.jpeg_quality = 90
        self.ext = ".jpg"
        
        # Paths inside the RPK
        self.flat_roof_asset_path = r"assets\3D_City_Design_Assets\Material_Library\FlatRoof"
        self.sloped_roof_asset_path = r"assets\3D_City_Design_Assets\Material_Library\SlopedRoof"

    def getParameterInfo(self):
        # 0. Input Multipatch
        param_in_fc = arcpy.Parameter(
            displayName="Input LoD2 Multipatch Feature Class",
            name="in_multipatch",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input")
        param_in_fc.filter.list = ["Multipatch"]

        # 1. Source Mode Dropdown
        param_mode = arcpy.Parameter(
            displayName="Imagery Source Mode",
            name="source_mode",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        param_mode.filter.type = "ValueList"
        param_mode.filter.list = ["Online XYZ/TMS Tiles", "Local Raster File"]
        param_mode.value = "Online XYZ/TMS Tiles"

        # 2. XYZ/TMS URL 
        param_url = arcpy.Parameter(
            displayName="XYZ/TMS Tile URL",
            name="xyz_url",
            datatype="GPString",
            parameterType="Optional",
            direction="Input")
        param_url.value = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

        # 3. Zoom Level 
        param_zoom = arcpy.Parameter(
            displayName="Zoom Level",
            name="zoom_level",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input")
        param_zoom.value = 19
        param_zoom.filter.type = "Range"
        param_zoom.filter.list = [1, 23]

        # 4. Local Raster 
        param_raster = arcpy.Parameter(
            displayName="Local Raster File (Orthophoto/UAV)",
            name="local_raster",
            datatype="GPRasterLayer", 
            parameterType="Optional",
            direction="Input")

        # 5. Output Image Format
        param_format = arcpy.Parameter(
            displayName="Output Image Format",
            name="image_format",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        param_format.filter.type = "ValueList"
        param_format.filter.list = ["JPEG", "PNG"]
        param_format.value = "JPEG"

        # 6. Output Rule Package (.rpk)
        param_out_rpk = arcpy.Parameter(
            displayName="Output Rule Package (.rpk)",
            name="out_rpk",
            datatype="DEFile",
            parameterType="Required",
            direction="Output")
        param_out_rpk.filter.list = ["rpk"]

        return [param_in_fc, param_mode, param_url, param_zoom, param_raster, param_format, param_out_rpk]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        # Enable/Disable inputs based on the selected mode
        if parameters[1].value:
            mode = parameters[1].valueAsText
            if mode == "Online XYZ/TMS Tiles":
                parameters[2].enabled = True   # URL
                parameters[3].enabled = True   # Zoom
                parameters[4].enabled = False  # Local Raster
            elif mode == "Local Raster File":
                parameters[2].enabled = False  # URL
                parameters[3].enabled = False  # Zoom
                parameters[4].enabled = True   # Local Raster
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        in_fc = parameters[0].valueAsText
        source_mode = parameters[1].valueAsText
        xyz_url = parameters[2].valueAsText
        self.zoom = int(parameters[3].valueAsText) if parameters[3].value else 19
        local_raster = parameters[4].valueAsText
        image_format = parameters[5].valueAsText
        out_rpk = parameters[6].valueAsText

        self.ext = ".jpg" if image_format == "JPEG" else ".png"

        pyt_dir = os.path.dirname(os.path.abspath(__file__))
        base_rpk_folder = os.path.join(pyt_dir, "BaseRPK")

        if not os.path.exists(base_rpk_folder):
            arcpy.AddError(f"Base RPK directory not found at: {base_rpk_folder}. Please ensure the unzipped template is there.")
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            sat_cache = os.path.join(temp_dir, "satellite_cache")
            rpk_staging = os.path.join(temp_dir, "rpk_staging")
            os.makedirs(sat_cache, exist_ok=True)

            arcpy.AddMessage(f"Using isolated temporary workspace: {temp_dir}")

            # STEP 1: Download or Extract
            if source_mode == "Online XYZ/TMS Tiles":
                if not xyz_url or "{x}" not in xyz_url.lower():
                    arcpy.AddError("Invalid XYZ URL. Must contain {x}, {y}, and {z}.")
                    return
                self.tile_url = xyz_url
                oid_list = self.export_roof_textures(in_fc, sat_cache)

            elif source_mode == "Local Raster File":
                if not local_raster:
                    arcpy.AddError("Please provide a local raster file.")
                    return
                oid_list = self.extract_local_raster(in_fc, local_raster, sat_cache, temp_dir)

            if not oid_list:
                arcpy.AddError("No textures were successfully generated. Aborting RPK creation.")
                return

            # STEP 2: Update Attributes
            self.update_attributes(in_fc)

            # STEP 3: Staging
            self.prepare_staging(oid_list, sat_cache, base_rpk_folder, rpk_staging)

            # STEP 4: Package
            self.package_rpk(rpk_staging, out_rpk)
            
        arcpy.AddMessage("Temporary cache and staging folders have been automatically wiped.")
        arcpy.AddMessage("Process Complete!")

    # ============================================================
    # WORKER METHODS
    # ============================================================
    def export_roof_textures(self, in_fc, sat_cache):
        arcpy.AddMessage("\n[1/4] Downloading satellite roof textures...")
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 SatelliteRoofTexturer/1.0"})

        total = int(arcpy.management.GetCount(in_fc)[0])
        arcpy.AddMessage(f"Processing {total} buildings...")
        arcpy.SetProgressor("step", f"Downloading textures for {total} buildings...", 0, total, 1)

        ok, fail, skip = 0, 0, 0
        oid_list = []

        with arcpy.da.SearchCursor(in_fc, ["OID@", "SHAPE@"]) as cursor:
            for i, (oid, shape) in enumerate(cursor):
                arcpy.SetProgressorLabel(f"Downloading OID {oid} ({i + 1}/{total})...")
                arcpy.SetProgressorPosition(i)

                if (i + 1) % 50 == 0:
                    arcpy.AddMessage(f"  ...processed {i + 1} of {total} buildings...")

                fname = f"satellite_roof_{oid}"
                out_path = os.path.join(sat_cache, fname + self.ext)
                oid_list.append(oid)

                if os.path.exists(out_path):
                    skip += 1
                    continue

                try:
                    min_lon, min_lat, max_lon, max_lat = get_wgs84_extent(shape)

                    if (max_lon - min_lon) < 1e-8 or (max_lat - min_lat) < 1e-8:
                        arcpy.AddWarning(f"OID {oid}: Degenerate geometry, skipping.")
                        fail += 1
                        continue

                    img = fetch_roof_image(min_lon, min_lat, max_lon, max_lat, self.zoom, session, self.tile_url, self.output_size)
                    
                    if self.ext == ".jpg":
                        img.save(out_path, "JPEG", quality=self.jpeg_quality)
                    else:
                        img.save(out_path, "PNG")
                    ok += 1

                except Exception as e:
                    arcpy.AddWarning(f"OID {oid} failed: {str(e)}")
                    fail += 1

        arcpy.ResetProgressor()
        arcpy.AddMessage(f"Download Results: {ok} OK, {skip} skipped, {fail} failed.")
        return oid_list

    def extract_local_raster(self, in_fc, local_raster, sat_cache, temp_dir):
        arcpy.AddMessage("\n[1/4] Extracting roof textures from local raster...")
        
        raster_desc = arcpy.Describe(local_raster)
        raster_sr = raster_desc.spatialReference
        
        total = int(arcpy.management.GetCount(in_fc)[0])
        arcpy.AddMessage(f"Processing {total} buildings...")
        arcpy.SetProgressor("step", f"Clipping raster for {total} buildings...", 0, total, 1)

        ok, fail, skip = 0, 0, 0
        oid_list = []

        with arcpy.da.SearchCursor(in_fc, ["OID@", "SHAPE@"]) as cursor:
            for i, (oid, shape) in enumerate(cursor):
                arcpy.SetProgressorLabel(f"Extracting OID {oid} ({i + 1}/{total})...")
                arcpy.SetProgressorPosition(i)

                if (i + 1) % 50 == 0:
                    arcpy.AddMessage(f"  ...processed {i + 1} of {total} buildings...")

                fname = f"satellite_roof_{oid}"
                out_path = os.path.join(sat_cache, fname + self.ext)
                oid_list.append(oid)

                if os.path.exists(out_path):
                    skip += 1
                    continue

                try:
                    projected_shape = shape.projectAs(raster_sr)
                    ext = projected_shape.extent

                    if (ext.XMax - ext.XMin) < 0.001 or (ext.YMax - ext.YMin) < 0.001:
                        arcpy.AddWarning(f"OID {oid}: Degenerate geometry, skipping.")
                        fail += 1
                        continue

                    rectangle = f"{ext.XMin} {ext.YMin} {ext.XMax} {ext.YMax}"
                    temp_clip = os.path.join(temp_dir, f"temp_clip_{oid}.tif")
                    
                    arcpy.management.Clip(
                        in_raster=local_raster,
                        rectangle=rectangle,
                        out_raster=temp_clip,
                        maintain_clipping_extent="MAINTAIN_EXTENT"
                    )
                    
                    with Image.open(temp_clip) as img:
                        if self.ext == ".jpg":
                            img = img.convert("RGB")
                        resized_img = img.resize(self.output_size, Image.LANCZOS)
                        
                        if self.ext == ".jpg":
                            resized_img.save(out_path, "JPEG", quality=self.jpeg_quality)
                        else:
                            resized_img.save(out_path, "PNG")
                            
                    if os.path.exists(temp_clip):
                        os.remove(temp_clip)
                        
                    ok += 1

                except Exception as e:
                    arcpy.AddWarning(f"OID {oid} failed: {str(e)}")
                    fail += 1

        arcpy.ResetProgressor()
        arcpy.AddMessage(f"Extraction Results: {ok} OK, {skip} skipped, {fail} failed.")
        return oid_list

    def update_attributes(self, in_fc):
        arcpy.AddMessage("\n[2/4] Updating roof texture attributes on multipatch...")
        existing = [f.name for f in arcpy.ListFields(in_fc)]

        if "Flat_Roof_Texture" not in existing:
            arcpy.management.AddField(in_fc, "Flat_Roof_Texture", "TEXT", field_length=100)
        
        if "Sloped_Roof_Texture" not in existing:
            arcpy.management.AddField(in_fc, "Sloped_Roof_Texture", "TEXT", field_length=100)

        with arcpy.da.UpdateCursor(in_fc, ["OID@", "Flat_Roof_Texture", "Sloped_Roof_Texture"]) as cur:
            for oid, _, __ in cur:
                val = f"satellite_roof_{oid}"
                cur.updateRow([oid, val, val])

        arcpy.AddMessage("Fields 'Flat_Roof_Texture' and 'Sloped_Roof_Texture' updated successfully.")

    def prepare_staging(self, oid_list, sat_cache, base_rpk_folder, rpk_staging):
        arcpy.AddMessage("\n[3/4] Preparing RPK staging and injecting assets...")
        shutil.copytree(base_rpk_folder, rpk_staging)

        flat_dir = os.path.join(rpk_staging, self.flat_roof_asset_path)
        sloped_dir = os.path.join(rpk_staging, self.sloped_roof_asset_path)
        os.makedirs(flat_dir, exist_ok=True)
        os.makedirs(sloped_dir, exist_ok=True)

        copied_flat = copied_sloped = 0
        for oid in oid_list:
            fname = f"satellite_roof_{oid}{self.ext}"
            src = os.path.join(sat_cache, fname)
            if not os.path.exists(src):
                continue
            
            shutil.copy2(src, os.path.join(flat_dir, fname))
            copied_flat += 1
            shutil.copy2(src, os.path.join(sloped_dir, fname))
            copied_sloped += 1

        arcpy.AddMessage(f"Injected {copied_flat} flat and {copied_sloped} sloped textures.")

        resolvemap_path = os.path.join(rpk_staging, ".resolvemap.xml")
        if os.path.exists(resolvemap_path):
            with open(resolvemap_path, "r", encoding="utf-8") as f:
                content = f.read()

            prefix_match = re.search(r'key="(/[^/]+)/assets/', content)
            project_prefix = prefix_match.group(1) if prefix_match else "/SFOSS_Help"

            mat_base = "assets/3D_City_Design_Assets/Material_Library"
            new_entries = []
            for oid in oid_list:
                # We map the exact file extension we generated
                fname = f"satellite_roof_{oid}{self.ext}"
                
                # If the CGA rule hardcoded ".jpg" but the user selected PNG, CityEngine might look for ".jpg".
                # To be safe, we map BOTH the expected .jpg key and the actual generated file key to the generated asset.
                fname_cga_key = f"satellite_roof_{oid}.jpg" 

                for subfolder in ["FlatRoof", "SlopedRoof"]:
                    value = f"{mat_base}/{subfolder}/{fname}"
                    
                    # 1. Map actual extension key
                    actual_key = f"{mat_base}/{subfolder}/{fname}"
                    new_entries.append(f'  <entry key="{actual_key}" value="{value}" />')
                    new_entries.append(f'  <entry key="{project_prefix}/{actual_key}" value="{value}" />')
                    
                    # 2. Map .jpg fallback key (in case CGA explicitly asks for .jpg but image is PNG)
                    if self.ext != ".jpg":
                        fallback_key = f"{mat_base}/{subfolder}/{fname_cga_key}"
                        new_entries.append(f'  <entry key="{fallback_key}" value="{value}" />')
                        new_entries.append(f'  <entry key="{project_prefix}/{fallback_key}" value="{value}" />')

            injection = "\n".join(new_entries)
            content = content.replace("</resolvemap>", injection + "\n</resolvemap>")

            with open(resolvemap_path, "w", encoding="utf-8") as f:
                f.write(content)
            arcpy.AddMessage(f"Updated .resolvemap.xml with {len(new_entries)} new entries.")
        else:
            arcpy.AddWarning(".resolvemap.xml not found in BaseRPK. Skipping resolvemap update.")

    def package_rpk(self, rpk_staging, out_rpk):
        arcpy.AddMessage(f"\n[4/4] Packaging final RPK to: {out_rpk}")
        with zipfile.ZipFile(out_rpk, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(rpk_staging):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    arcname = os.path.relpath(fpath, rpk_staging)
                    zf.write(fpath, arcname)

        size_mb = os.path.getsize(out_rpk) / (1024 * 1024)
        arcpy.AddMessage(f"RPK created successfully: {size_mb:.1f} MB")