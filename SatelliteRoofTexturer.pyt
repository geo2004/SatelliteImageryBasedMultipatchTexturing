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
        self.label = "Texture Roof Multipatch From Satellite Imagery"
        self.description = "Mass-textures multipatch building roofs using satellite tile imagery."
        self.canRunInBackground = False

        # Configuration Constants
        self.zoom = 19
        self.tile_url = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        self.output_size = (512, 512)
        self.jpeg_quality = 90
        
        # Paths inside the RPK
        self.flat_roof_asset_path = r"assets\3D_City_Design_Assets\Material_Library\FlatRoof"
        self.sloped_roof_asset_path = r"assets\3D_City_Design_Assets\Material_Library\SlopedRoof"

    def getParameterInfo(self):
        # Param 0: Input Multipatch Feature Class / Layer
        param_in_fc = arcpy.Parameter(
            displayName="Input Multipatch Feature Class",
            name="in_multipatch",
            datatype="GPFeatureLayer",  
            parameterType="Required",
            direction="Input")
        param_in_fc.filter.list = ["MultiPatch"] 

        # Param 1: Output Rule Package (.rpk)
        param_out_rpk = arcpy.Parameter(
            displayName="Output Rule Package (.rpk)",
            name="out_rpk",
            datatype="DEFile",
            parameterType="Required",
            direction="Output")
        param_out_rpk.filter.list = ["rpk"]

        return [param_in_fc, param_out_rpk]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        in_fc = parameters[0].valueAsText
        out_rpk = parameters[1].valueAsText

        # Dynamically locate the BaseRPK folder relative to this .pyt file
        pyt_dir = os.path.dirname(os.path.abspath(__file__))
        base_rpk_folder = os.path.join(pyt_dir, "BaseRPK")

        if not os.path.exists(base_rpk_folder):
            arcpy.AddError(f"Base RPK directory not found at: {base_rpk_folder}. Please ensure the unzipped template is there.")
            return

        # Use tempfile to guarantee a clean cache/staging area that auto-deletes
        with tempfile.TemporaryDirectory() as temp_dir:
            sat_cache = os.path.join(temp_dir, "satellite_cache")
            rpk_staging = os.path.join(temp_dir, "rpk_staging")
            os.makedirs(sat_cache, exist_ok=True)

            arcpy.AddMessage(f"Using isolated temporary workspace: {temp_dir}")

            # STEP 1: Download
            oid_list = self.export_roof_textures(in_fc, sat_cache)

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

        ok, fail, skip = 0, 0, 0
        oid_list = []

        with arcpy.da.SearchCursor(in_fc, ["OID@", "SHAPE@"]) as cursor:
            for oid, shape in cursor:
                fname = f"satellite_roof_{oid}"
                out_path = os.path.join(sat_cache, fname + ".jpg")
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
                    img.save(out_path, "JPEG", quality=self.jpeg_quality)
                    ok += 1

                except Exception as e:
                    arcpy.AddWarning(f"OID {oid} failed: {str(e)}")
                    fail += 1

        arcpy.AddMessage(f"Download Results: {ok} OK, {skip} skipped, {fail} failed.")
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
            fname = f"satellite_roof_{oid}.jpg"
            src = os.path.join(sat_cache, fname)
            if not os.path.exists(src):
                continue
            
            shutil.copy2(src, os.path.join(flat_dir, fname))
            copied_flat += 1
            shutil.copy2(src, os.path.join(sloped_dir, fname))
            copied_sloped += 1

        arcpy.AddMessage(f"Injected {copied_flat} flat and {copied_sloped} sloped textures.")

        # Update .resolvemap.xml
        resolvemap_path = os.path.join(rpk_staging, ".resolvemap.xml")
        if os.path.exists(resolvemap_path):
            with open(resolvemap_path, "r", encoding="utf-8") as f:
                content = f.read()

            prefix_match = re.search(r'key="(/[^/]+)/assets/', content)
            project_prefix = prefix_match.group(1) if prefix_match else "/SFOSS_Help"

            mat_base = "assets/3D_City_Design_Assets/Material_Library"
            new_entries = []
            for oid in oid_list:
                fname = f"satellite_roof_{oid}.jpg"
                for subfolder in ["FlatRoof", "SlopedRoof"]:
                    value = f"{mat_base}/{subfolder}/{fname}"
                    new_entries.append(f'  <entry key="{value}" value="{value}" />')
                    new_entries.append(f'  <entry key="{project_prefix}/{value}" value="{value}" />')

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