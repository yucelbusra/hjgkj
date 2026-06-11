# -*- coding: utf-8 -*-
"""
FACADE-BY-FACADE EXPORT
Combines adjacent BASIC WALLS into a single facade
Openings (doors, windows, storefronts) remain separate
"""
from Autodesk.Revit.DB import (
    FilteredElementCollector, Wall, BuiltInParameter, LocationPoint, Arc, XYZ,
    FamilyInstance, BuiltInCategory, Opening, Level
)

from pyrevit import revit, forms
import csv
import os
import codecs
import math
import json

doc = revit.doc
uidoc = revit.uidoc

# ========== SETTINGS ==========
import clr
clr.AddReference('System.Windows.Forms')
from System.Windows.Forms import FolderBrowserDialog, DialogResult
import os

# Open folder selection dialog
dialog = FolderBrowserDialog()
dialog.Description = "Select Output Folder for Revit Walls"

# Use user's Desktop as initial directory (works on any computer)
initial_dir = os.path.join(os.path.expanduser("~"), "Desktop")

# Fallback to user's home directory if Desktop doesn't exist
if not os.path.exists(initial_dir):
    initial_dir = os.path.expanduser("~")

dialog.SelectedPath = initial_dir

# Show dialog and get result
result = dialog.ShowDialog()

if result == DialogResult.OK:
    OUTPUT_DIR = dialog.SelectedPath
    print("Selected output folder: " + OUTPUT_DIR)
else:
    print("No folder selected. Exiting...")
    import sys
    sys.exit(0)  # FIX: exit cleanly instead of letting None propagate to path joins



WALLS_FILE = "walls.csv"
OPENINGS_FILE = "wall_openings.csv"
MAPPING_FILE = "wall_mapping.csv"
WALLS_PATH = os.path.join(OUTPUT_DIR, WALLS_FILE)
OPENINGS_PATH = os.path.join(OUTPUT_DIR, OPENINGS_FILE)
MAPPING_PATH = os.path.join(OUTPUT_DIR, MAPPING_FILE)

if not os.path.isdir(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# ========== HELPERS ==========
try:
    basestring
except NameError:
    basestring = str

def rnum(v, nd=4):
    try: return round(float(v), nd)
    except: return ""

def xyz_str(p, nd=4):
    if not p: return ""
    return "({},{},{})".format(rnum(p.X, nd), rnum(p.Y, nd), rnum(p.Z, nd))

def get_bip(name):
    try:
        return getattr(BuiltInParameter, name)
    except:
        return None

def get_param(elem, key):
    if key is None:
        return None
    if not isinstance(key, basestring):
        try:
            p = elem.get_Parameter(key)
            if p: return p
        except: pass
    if isinstance(key, basestring):
        try:
            p = elem.LookupParameter(key)
            if p: return p
        except: pass
    return None

def get_param_val(elem, key, as_string=False):
    p = get_param(elem, key)
    if not p: return ""
    try:
        return p.AsValueString() if as_string else p.AsDouble()
    except:
        try:
            return p.AsInteger()
        except:
            try:
                return p.AsString() or ""
            except:
                return ""

def level_name(elem):
    try:
        lvl_id = elem.LevelId
        if lvl_id and lvl_id.IntegerValue > 0:
            lvl = doc.GetElement(lvl_id)
            return getattr(lvl, "Name", "")
    except:
        pass
    return (get_param_val(elem, get_bip("FAMILY_LEVEL_PARAM"), as_string=True)
            or get_param_val(elem, "Level", as_string=True)
            or "")

def get_combined_bounding_box(walls):
    """Calculate the combined bounding box of multiple walls"""
    if not walls:
        return None
    
    min_x = min_y = min_z = float('inf')
    max_x = max_y = max_z = float('-inf')
    
    for wall in walls:
        bbox = wall.get_BoundingBox(None)
        if bbox:
            min_x = min(min_x, bbox.Min.X)
            min_y = min(min_y, bbox.Min.Y)
            min_z = min(min_z, bbox.Min.Z)
            max_x = max(max_x, bbox.Max.X)
            max_y = max(max_y, bbox.Max.Y)
            max_z = max(max_z, bbox.Max.Z)
    
    if min_x == float('inf'):
        return None
    
    return {
        'min': XYZ(min_x, min_y, min_z),
        'max': XYZ(max_x, max_y, max_z),
        'center': XYZ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0)
    }

def calculate_combined_dimensions(bbox_dict, walls=None):
    """Calculate length, width (thickness), and height from combined bounding box.
    Reads true wall thickness from wall.Width if walls list is provided."""
    min_pt = bbox_dict['min']
    max_pt = bbox_dict['max']

    delta_x = abs(max_pt.X - min_pt.X)
    delta_y = abs(max_pt.Y - min_pt.Y)

    # Length = facade extent (larger horizontal dimension)
    length_ft = max(delta_x, delta_y)
    # Height = vertical
    height_ft = abs(max_pt.Z - min_pt.Z)

    # Width = true wall thickness from first wall, NOT from bbox
    # (bbox min dimension is unreliable for angled walls)
    width_ft = min(delta_x, delta_y)  # fallback
    if walls:
        try:
            width_ft = walls[0].Width
        except:
            pass

    return length_ft, width_ft, height_ft

def create_synthetic_curve_from_bbox(bbox_dict):
    """Create start/end points representing the combined facade extent"""
    min_pt = bbox_dict['min']
    max_pt = bbox_dict['max']
    
    delta_x = abs(max_pt.X - min_pt.X)
    delta_y = abs(max_pt.Y - min_pt.Y)
    
    if delta_x > delta_y:
        # Facade runs along X-axis
        p0 = XYZ(min_pt.X, (min_pt.Y + max_pt.Y) / 2.0, min_pt.Z)
        p1 = XYZ(max_pt.X, (min_pt.Y + max_pt.Y) / 2.0, min_pt.Z)
    else:
        # Facade runs along Y-axis
        p0 = XYZ((min_pt.X + max_pt.X) / 2.0, min_pt.Y, min_pt.Z)
        p1 = XYZ((min_pt.X + max_pt.X) / 2.0, max_pt.Y, min_pt.Z)
    
    mid = XYZ((p0.X + p1.X) / 2.0, (p0.Y + p1.Y) / 2.0, (p0.Z + p1.Z) / 2.0)
    length = math.sqrt((p1.X - p0.X)**2 + (p1.Y - p0.Y)**2 + (p1.Z - p0.Z)**2)
    
    return {
        "curve_type": "Line (Combined)",
        "p0": xyz_str(p0),
        "p1": xyz_str(p1),
        "mid": xyz_str(mid),
        "length_ft": rnum(length)
    }

def get_opening_category(elem):
    """Determine if opening is a door, window, wall opening, or other type."""
    # Native wall openings (Opening class) have no useful Category.Name
    try:
        from Autodesk.Revit.DB import Opening as RvtOpening
        if isinstance(elem, RvtOpening):
            return "Wall Opening"
    except:
        pass
    try:
        cat = elem.Category
        if cat:
            cat_name = cat.Name
            if "Door" in cat_name:
                return "Door"
            elif "Window" in cat_name:
                return "Window"
            elif "Opening" in cat_name:
                return "Wall Opening"
            else:
                return cat_name
    except:
        pass
    return "Unknown"

def is_wall_opening(elem):
    """True if elem is a native Revit Wall Opening (Opening class, not a hosted family)."""
    try:
        from Autodesk.Revit.DB import Opening as RvtOpening
        return isinstance(elem, RvtOpening)
    except:
        return False


def get_wall_opening_geometry(opening, wall_base_z, facade_bbox, facade_axis_x):
    """
    Extract width, height, sill height and horizontal position from a
    native Wall Opening element using its boundary sketch curves.
    Returns (left_edge_ft, center_ft, right_edge_ft, width_ft, height_ft, sill_ft)
    or None on failure.
    """
    try:
        from Autodesk.Revit.DB import Opening as RvtOpening
        bbox = opening.get_BoundingBox(None)
        if not bbox:
            return None

        b_min = bbox.Min
        b_max = bbox.Max

        # Width along the facade axis
        if facade_axis_x:
            width_ft  = abs(b_max.X - b_min.X)
            center_ft_world = (b_min.X + b_max.X) / 2.0
            facade_start = facade_bbox['min'].X
        else:
            width_ft  = abs(b_max.Y - b_min.Y)
            center_ft_world = (b_min.Y + b_max.Y) / 2.0
            facade_start = facade_bbox['min'].Y

        height_ft = abs(b_max.Z - b_min.Z)
        sill_ft   = b_min.Z - wall_base_z

        # Horizontal position relative to facade start
        left_edge_ft  = (b_min.X if facade_axis_x else b_min.Y) - facade_start
        right_edge_ft = left_edge_ft + width_ft
        center_ft     = left_edge_ft + width_ft / 2.0

        return (left_edge_ft, center_ft, right_edge_ft, width_ft, height_ft, sill_ft)
    except Exception as e:
        print("  [WallOpening] geometry error: {}".format(e))
        return None


def get_opening_dimensions(opening, opening_type_elem):
    """Get opening dimensions from type parameters"""
    width = ""
    height = ""
    thickness = ""
    
    width = (get_param_val(opening, get_bip("DOOR_WIDTH")) or
             get_param_val(opening, get_bip("WINDOW_WIDTH")) or
             get_param_val(opening, get_bip("GENERIC_WIDTH")) or
             get_param_val(opening, "Width") or "")
    
    if not width and opening_type_elem:
        width = (get_param_val(opening_type_elem, get_bip("DOOR_WIDTH")) or
                 get_param_val(opening_type_elem, get_bip("WINDOW_WIDTH")) or
                 get_param_val(opening_type_elem, get_bip("GENERIC_WIDTH")) or
                 get_param_val(opening_type_elem, "Width") or
                 get_param_val(opening_type_elem, "Rough Width") or "")
    
    height = (get_param_val(opening, get_bip("DOOR_HEIGHT")) or
              get_param_val(opening, get_bip("WINDOW_HEIGHT")) or
              get_param_val(opening, get_bip("GENERIC_HEIGHT")) or
              get_param_val(opening, "Height") or "")
    
    if not height and opening_type_elem:
        height = (get_param_val(opening_type_elem, get_bip("DOOR_HEIGHT")) or
                  get_param_val(opening_type_elem, get_bip("WINDOW_HEIGHT")) or
                  get_param_val(opening_type_elem, get_bip("GENERIC_HEIGHT")) or
                  get_param_val(opening_type_elem, "Height") or
                  get_param_val(opening_type_elem, "Rough Height") or "")
    
    thickness = (get_param_val(opening, get_bip("GENERIC_THICKNESS")) or
                 get_param_val(opening, "Thickness") or "")
    
    if not thickness and opening_type_elem:
        thickness = (get_param_val(opening_type_elem, get_bip("GENERIC_THICKNESS")) or
                     get_param_val(opening_type_elem, "Thickness") or "")
    
    return rnum(width), rnum(height), rnum(thickness)

def calculate_opening_position_on_combined_facade(opening, combined_bbox, is_curtain_wall=False):
    """
    Calculate opening position relative to combined facade extent.
    Returns horizontal position AND wall-relative vertical position.
    Works for doors, windows, and curtain walls (storefronts).
    """
    try:
        # Determine facade axis
        min_pt = combined_bbox['min']
        max_pt = combined_bbox['max']
        delta_x = abs(max_pt.X - min_pt.X)
        delta_y = abs(max_pt.Y - min_pt.Y)
        
        # Wall base elevation for vertical normalization
        wall_base_z = min_pt.Z
        
        if is_curtain_wall:
            # For curtain walls, use bounding box center
            bbox = opening.get_BoundingBox(None)
            if not bbox:
                return None
            
            opening_point = XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0
            )
            
            # Calculate width for curtain wall
            if delta_x > delta_y:
                width = abs(bbox.Max.X - bbox.Min.X)
            else:
                width = abs(bbox.Max.Y - bbox.Min.Y)
            
            # Calculate height for curtain wall
            height = abs(bbox.Max.Z - bbox.Min.Z)
            
            # CRITICAL: Wall-relative sill height (bottom of opening)
            sill_height_ft = bbox.Min.Z - wall_base_z
            
        else:
            # For doors/windows, use location point
            opening_location = opening.Location
            if not isinstance(opening_location, LocationPoint):
                return None
            
            opening_point = opening_location.Point
            
            # Get width from parameters (in feet)
            opening_type_elem = doc.GetElement(opening.GetTypeId())
            width_val = (get_param_val(opening, get_bip("DOOR_WIDTH")) or
                        get_param_val(opening, get_bip("WINDOW_WIDTH")) or
                        get_param_val(opening, get_bip("GENERIC_WIDTH")) or
                        get_param_val(opening, "Width"))
            
            if not width_val and opening_type_elem:
                width_val = (get_param_val(opening_type_elem, get_bip("DOOR_WIDTH")) or
                           get_param_val(opening_type_elem, get_bip("WINDOW_WIDTH")) or
                           get_param_val(opening_type_elem, get_bip("GENERIC_WIDTH")) or
                           get_param_val(opening_type_elem, "Width") or
                           get_param_val(opening_type_elem, "Rough Width"))
            
            width = float(width_val) if width_val else 0.0
            
            # Get height from parameters (in feet)
            height_val = (get_param_val(opening, get_bip("DOOR_HEIGHT")) or
                         get_param_val(opening, get_bip("WINDOW_HEIGHT")) or
                         get_param_val(opening, get_bip("GENERIC_HEIGHT")) or
                         get_param_val(opening, "Height"))
            
            if not height_val and opening_type_elem:
                height_val = (get_param_val(opening_type_elem, get_bip("DOOR_HEIGHT")) or
                             get_param_val(opening_type_elem, get_bip("WINDOW_HEIGHT")) or
                             get_param_val(opening_type_elem, get_bip("GENERIC_HEIGHT")) or
                             get_param_val(opening_type_elem, "Height") or
                             get_param_val(opening_type_elem, "Rough Height"))
            
            height = float(height_val) if height_val else 0.0
            
            # CRITICAL: Get sill height parameter (already wall-relative in Revit)
            sill_height_param = get_param_val(opening, get_bip("INSTANCE_SILL_HEIGHT_PARAM"))
            if not sill_height_param:
                sill_height_param = get_param_val(opening, "Sill Height")

            # Sill height parameter is ALWAYS wall-relative in Revit
            if sill_height_param and sill_height_param != "":
                try:
                    sill_height_ft = float(sill_height_param)
                except:
                    sill_height_ft = 0.0
            else:
                # Fallback: For doors, sill is at floor level (0)
                # For windows, calculate from location point
                category_name = opening.Category.Name if opening.Category else ""
                if "Door" in category_name:
                    sill_height_ft = 0.0  # Doors start at floor level
                else:
                    # Windows: location point is at center, so subtract half height
                    opening_center_z = opening_point.Z
                    sill_height_ft = (opening_center_z - (height / 2.0)) - wall_base_z
                    sill_height_ft = max(0.0, sill_height_ft)  # Can't be negative
            
            # Adjust for opening orientation (horizontal positioning refinement)
            try:
                if hasattr(opening, 'FacingOrientation'):
                    facing = opening.FacingOrientation
                    # Project the facing direction onto the facade axis to determine offset
                    if delta_x > delta_y:
                        # Facade along X-axis - use facing Y component
                        pass
                    else:
                        # Facade along Y-axis - use facing X component
                        pass
            except:
                pass
        
        # Calculate horizontal position along facade
        if delta_x > delta_y:
            # Facade along X-axis
            facade_start = min_pt.X
            facade_length = max_pt.X - min_pt.X
            position_ft = opening_point.X - facade_start
        else:
            # Facade along Y-axis
            facade_start = min_pt.Y
            facade_length = max_pt.Y - min_pt.Y
            position_ft = opening_point.Y - facade_start
        
        # Clamp position to facade bounds
        position_ft = max(0, min(facade_length, position_ft))
        
        # Calculate edges — opening is centered at position_ft horizontally
        left_edge_ft  = position_ft - (width / 2.0)
        right_edge_ft = position_ft + (width / 2.0)

        # NOTE: Do NOT clamp edges to facade bounds — openings that span the
        # facade boundary should be reported at their true size so the panel
        # calculator can compute correct clearance zones.
        
        # Return tuple: (left_edge, center_position, right_edge, width, height, sill_height)
        return (left_edge_ft, position_ft, right_edge_ft, width, height, sill_height_ft)
        
    except Exception as e:
        print("  Error calculating opening position: {}".format(str(e)))
        return None

# ========== GET SELECTED WALLS ==========
sel_ids = list(uidoc.Selection.GetElementIds())
if not sel_ids:
    forms.alert("Please select one or more walls before running this tool.", exitscript=True)

selected_walls = []
selected_wall_ids = set()
for eid in sel_ids:
    elem = doc.GetElement(eid)
    if isinstance(elem, Wall):
        selected_walls.append(elem)
        selected_wall_ids.add(elem.Id.IntegerValue)

if not selected_walls:
    forms.alert("No walls found in the current selection.", exitscript=True)

print("Processing {} selected walls...".format(len(selected_walls)))

# Separate basic walls from curtain walls
basic_walls = []
curtain_walls = []


for wall in selected_walls:
    try:
        wall_type = doc.GetElement(wall.GetTypeId())
        kind_name = str(wall.WallType.Kind)
        if kind_name.lower() == "basic":
            basic_walls.append(wall)
        else:
            curtain_walls.append(wall)
    except:
        basic_walls.append(wall)

print("  {} basic walls (will be combined into 1 wall)".format(len(basic_walls)))
print("  {} curtain/storefront walls (will be exported as openings)".format(len(curtain_walls)))

# ----------------------------------------------------
# EXPORT ALL PROJECT LEVEL ELEVATIONS (in inches)
# ----------------------------------------------------



# ========== EXPORT COMBINED BASIC WALLS CSV ==========
print("\nExporting combined basic walls to CSV...")

if not basic_walls:
    print("  No basic walls to export")
    # Create empty file with headers
    with codecs.open(WALLS_PATH, mode="w", encoding="utf-8") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow([
            "WallId","WallKind","TypeName","FamilyName","Function","IsStructural",
            "Width(ft)","Length(ft)","UnconnectedHeight(ft)","Area(sf)","Volume(cf)",
            "BaseLevel","BaseOffset(ft)","TopConstraint","TopOffset(ft)","LocationLine",
            "CurveType","Start(X,Y,Z)","End(X,Y,Z)","Mid(X,Y,Z)","CurveLength(ft)",
            "ArcRadius(ft)","ArcAngle(rad)","ArcCenter(X,Y,Z)","AxisDir(unit XYZ)","Normal(unit XYZ)",
            "Layers","WallCount", "LevelElevations(in)"
        ])
else:
    try:
        with codecs.open(WALLS_PATH, mode="w", encoding="utf-8") as f:
            csv_writer = csv.writer(f)
            csv_writer.writerow([
                "WallId","WallKind","TypeName","FamilyName","Function","IsStructural",
                "Width(ft)","Length(ft)","UnconnectedHeight(ft)","Area(sf)","Volume(cf)",
                "BaseLevel","BaseOffset(ft)","TopConstraint","TopOffset(ft)","LocationLine",
                "CurveType","Start(X,Y,Z)","End(X,Y,Z)","Mid(X,Y,Z)","CurveLength(ft)",
                "ArcRadius(ft)","ArcAngle(rad)","ArcCenter(X,Y,Z)","AxisDir(unit XYZ)","Normal(unit XYZ)",
                "Layers","WallCount", "LevelElevations(in)"
            ])
            
            levels = list(FilteredElementCollector(doc).OfClass(Level))
            level_elev_in = sorted([int(round(l.Elevation * 12)) for l in levels])

            # Get combined bounding box
            combined_bbox = get_combined_bounding_box(basic_walls)
            if not combined_bbox:
                print("  ERROR: Could not calculate combined bounding box")
            else:
                # Calculate combined dimensions
                length_ft, width_ft, height_ft = calculate_combined_dimensions(combined_bbox, basic_walls)
                
                # Create synthetic curve info
                ci = create_synthetic_curve_from_bbox(combined_bbox)
                
                # Use first wall's properties as representative
                first_wall = basic_walls[0]
                wall_type = doc.GetElement(first_wall.GetTypeId())
                kind_name = "Basic (Combined)"
                type_name = getattr(wall_type, "Name", "") or ""
                family_name = getattr(wall_type, "FamilyName", "") or ""
                
                function_str = (get_param_val(first_wall, "Function", as_string=True) or
                               get_param_val(first_wall, get_bip("WALL_ATTR_FUNCTION_PARAM"), as_string=True) or "")
                
                is_struct = bool(getattr(first_wall, "Structural", False))
                
                # Calculate combined area and volume
                area_sf = rnum(length_ft * height_ft)
                vol_cf = rnum(length_ft * height_ft * width_ft)
                
                base_lvl = level_name(first_wall)
                base_off = (get_param_val(first_wall, get_bip("WALL_BASE_OFFSET")) or
                           get_param_val(first_wall, "Base Offset") or "")
                top_con = (get_param_val(first_wall, "Top Constraint", as_string=True) or
                          get_param_val(first_wall, get_bip("WALL_HEIGHT_TYPE"), as_string=True) or "")
                top_off = (get_param_val(first_wall, get_bip("WALL_TOP_OFFSET")) or
                          get_param_val(first_wall, "Top Offset") or "")
                loc_line = (get_param_val(first_wall, get_bip("WALL_KEY_REF_PARAM"), as_string=True) or
                           get_param_val(first_wall, "Location Line", as_string=True) or "")
                
                # Get layers from first wall
                layers_info = []
                try:
                    compound_structure = wall_type.GetCompoundStructure()
                    if compound_structure:
                        for layer in compound_structure.GetLayers():
                            function_name = str(layer.Function)
                            material_id = layer.MaterialId
                            material_name = "<By Category>"
                            if material_id.IntegerValue > 0:
                                material = doc.GetElement(material_id)
                                material_name = material.Name if material else "<By Category>"
                            thickness_in = round(layer.Width * 12, 3)
                            wraps = "Yes" if layer.LayerCapFlag else "No"
                            layers_info.append("{} | {} | {} in | Wrap:{}".format(
                                function_name, material_name, thickness_in, wraps
                            ))
                except:
                    pass
                
                layers_str = " || ".join(layers_info) if layers_info else "No Layers"
                
                # Combined wall ID - just use first wall's ID
                combined_id = first_wall.Id.IntegerValue
                
                # Store mapping for reference
                combined_wall_mapping = {
                    'combined_id': combined_id,
                    'original_wall_ids': [wall.Id.IntegerValue for wall in basic_walls]
                }
                
                # Write the combined wall row
                csv_writer.writerow([
                    combined_id,
                    kind_name,
                    type_name,
                    family_name,
                    function_str,
                    is_struct,
                    rnum(width_ft),
                    rnum(length_ft),
                    rnum(height_ft),
                    area_sf,
                    vol_cf,
                    base_lvl,
                    rnum(base_off),
                    top_con,
                    rnum(top_off),
                    loc_line,
                    ci["curve_type"],
                    ci["p0"],
                    ci["p1"],
                    ci["mid"],
                    ci["length_ft"],
                    "",
                    "",
                    "",
                    "",
                    "",
                    layers_str,
                    str(len(basic_walls)),
                    json.dumps(level_elev_in)
                ])
                
                print("  Combined {} basic walls into single facade:".format(len(basic_walls)))
                print("    Length: {} ft".format(rnum(length_ft, 2)))
                print("    Height: {} ft".format(rnum(height_ft, 2)))
                print("    Area: {} sf".format(rnum(area_sf, 2)))
        
        print("Walls exported successfully to: {}".format(WALLS_PATH))
        
    except IOError as e:
        print("\nERROR: Cannot write to walls.csv - file may be open.")
        print("Error: {}".format(str(e)))
        raise

# ========== COLLECT OPENINGS FROM SELECTED WALLS ==========
print("\nCollecting openings from selected walls...")

# Collect doors from basic walls
all_doors = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType()
doors = [d for d in all_doors if hasattr(d, 'Host') and d.Host and d.Host.Id.IntegerValue in selected_wall_ids]

# Collect windows from basic walls
all_windows = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Windows).WhereElementIsNotElementType()
windows = [win for win in all_windows if hasattr(win, 'Host') and win.Host and win.Host.Id.IntegerValue in selected_wall_ids]

# Collect wall openings from basic walls
all_openings = FilteredElementCollector(doc).OfClass(Opening).WhereElementIsNotElementType()
openings = [op for op in all_openings if hasattr(op, 'Host') and op.Host and op.Host.Id.IntegerValue in selected_wall_ids]

# Add curtain walls as storefronts
all_openings_list = list(doors) + list(windows) + list(openings) + curtain_walls

print("  {} doors".format(len(doors)))
print("  {} windows".format(len(windows)))
print("  {} wall openings".format(len(openings)))
print("  {} curtain/storefront walls".format(len(curtain_walls)))
print("  Total openings to export: {}".format(len(all_openings_list)))

# ========== EXPORT OPENINGS CSV ==========
print("\nExporting openings to CSV...")

successful_exports = 0
failed_exports = 0

try:
    with codecs.open(OPENINGS_PATH, mode="w", encoding="utf-8") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow([
            "OpeningId","OpeningType","Category","TypeName","FamilyName",
            "HostWallId","HostWallType","Level","SillHeight(ft)",
            "Width(ft)","Height(ft)","Thickness(ft)",
            "PositionAlongWall(ft)","LeftEdgeAlongWall(ft)","RightEdgeAlongWall(ft)",
            "Location(X,Y,Z)","FacingOrientation","HandOrientation",
            "FromRoom","ToRoom","Mark","Comments","Area(sf)"
        ])
        
        # Get combined facade ID and bbox for position calculations
        combined_facade_id = ""
        combined_bbox = None
        
        if basic_walls:
            combined_facade_id = basic_walls[0].Id.IntegerValue
            combined_bbox = get_combined_bounding_box(basic_walls)
        
        for opening in all_openings_list:
            opening_id = "unknown"  # FIX: initialize before try so except can always log it
            try:
                opening_id = opening.Id.IntegerValue
                
                # Classify curtain walls as storefronts
                if isinstance(opening, Wall):
                    opening_type = "Storefront"
                else:
                    opening_type = get_opening_category(opening)
                
                category = opening.Category.Name if opening.Category else ""
                opening_type_elem = doc.GetElement(opening.GetTypeId())
                type_name = getattr(opening_type_elem, "Name", "") or ""
                family_name = getattr(opening_type_elem, "FamilyName", "") if hasattr(opening_type_elem, 'FamilyName') else ""
                
                # Host wall is always the combined facade
                host_wall_id = combined_facade_id
                host_wall_type = getattr(doc.GetElement(basic_walls[0].GetTypeId()), "Name", "") if basic_walls else ""
                
                # Level and sill height
                lvl = level_name(opening)
                if is_wall_opening(opening):
                    # Native wall openings have no sill parameter -- derived from bbox in pos_data
                    sill_height = ""
                else:
                    sill_height = (get_param_val(opening, get_bip("INSTANCE_SILL_HEIGHT_PARAM")) or
                                  get_param_val(opening, "Sill Height") or "")
                
                # Dimensions
                if isinstance(opening, Wall):
                    # Curtain wall dimensions
                    bbox = opening.get_BoundingBox(None)
                    if bbox:
                        width = rnum(abs(bbox.Max.X - bbox.Min.X) if abs(bbox.Max.X - bbox.Min.X) > abs(bbox.Max.Y - bbox.Min.Y) else abs(bbox.Max.Y - bbox.Min.Y))
                        height = rnum(abs(bbox.Max.Z - bbox.Min.Z))
                    else:
                        width = ""
                        height = ""
                    thickness = rnum(getattr(opening, 'Width', 0.0))
                elif is_wall_opening(opening):
                    # Native Wall Opening - dimensions from bounding box
                    bbox = opening.get_BoundingBox(None)
                    if bbox:
                        delta_x = abs(bbox.Max.X - bbox.Min.X)
                        delta_y = abs(bbox.Max.Y - bbox.Min.Y)
                        width   = rnum(delta_x if delta_x > delta_y else delta_y)
                        height  = rnum(abs(bbox.Max.Z - bbox.Min.Z))
                    else:
                        width = ""
                        height = ""
                    thickness = ""
                else:
                    # Regular opening dimensions (door / window family)
                    width, height, thickness = get_opening_dimensions(opening, opening_type_elem)
                
                # Position calculation relative to combined facade
                position_along_wall = ""
                left_edge = ""
                right_edge = ""
                pos_data = None  # FIX: always initialize before conditional assignment

                if combined_bbox:
                    # Determine facade axis once
                    _min = combined_bbox['min']
                    _max = combined_bbox['max']
                    _facade_axis_x = abs(_max.X - _min.X) > abs(_max.Y - _min.Y)
                    _wall_base_z   = _min.Z

                    if is_wall_opening(opening):
                        pos_data = get_wall_opening_geometry(
                            opening, _wall_base_z, combined_bbox, _facade_axis_x)
                    else:
                        pos_data = calculate_opening_position_on_combined_facade(
                            opening,
                            combined_bbox,
                            is_curtain_wall=isinstance(opening, Wall)
                        )

                if pos_data:
                    left_edge_ft, pos_ft, right_edge_ft, width_calc, height_calc, sill_ft = pos_data
                    position_along_wall = rnum(pos_ft)
                    left_edge = rnum(left_edge_ft)
                    right_edge = rnum(right_edge_ft)
                    sill_height = rnum(sill_ft)
                    # Use calculated width/height if missing
                    if not width:
                        width = rnum(width_calc)
                    if not height:
                        height = rnum(height_calc)
                
                # Location
                location_pt = ""
                try:
                    if isinstance(opening, Wall) or is_wall_opening(opening):
                        # Curtain walls and native wall openings: use bbox center
                        bbox = opening.get_BoundingBox(None)
                        if bbox:
                            center = XYZ(
                                (bbox.Min.X + bbox.Max.X) / 2.0,
                                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                                (bbox.Min.Z + bbox.Max.Z) / 2.0
                            )
                            location_pt = xyz_str(center)
                    else:
                        loc = opening.Location
                        if isinstance(loc, LocationPoint):
                            location_pt = xyz_str(loc.Point)
                except:
                    pass
                
                # Orientation
                facing_orient = ""
                hand_orient = ""
                try:
                    if hasattr(opening, 'FacingOrientation'):
                        facing_orient = xyz_str(opening.FacingOrientation)
                    if hasattr(opening, 'HandOrientation'):
                        hand_orient = xyz_str(opening.HandOrientation)
                except:
                    pass
                
                # Room information
                from_room = ""
                to_room = ""
                if not isinstance(opening, Wall):
                    from_room = (get_param_val(opening, get_bip("DOOR_FROM_ROOM"), as_string=True) or
                                get_param_val(opening, "From Room", as_string=True) or "")
                    to_room = (get_param_val(opening, get_bip("DOOR_TO_ROOM"), as_string=True) or
                              get_param_val(opening, "To Room", as_string=True) or "")
                
                # Mark and comments
                mark = (get_param_val(opening, get_bip("ALL_MODEL_MARK"), as_string=True) or
                       get_param_val(opening, "Mark", as_string=True) or "")
                
                comments = (get_param_val(opening, get_bip("ALL_MODEL_INSTANCE_COMMENTS"), as_string=True) or
                           get_param_val(opening, "Comments", as_string=True) or "")
                
                # Area
                area = ""
                try:
                    if width and height:
                        area = rnum(float(width) * float(height))
                except:
                    pass
                
                # Write row
                csv_writer.writerow([
                    opening_id, opening_type, category, type_name, family_name,
                    host_wall_id, host_wall_type, lvl, rnum(sill_height),
                    width, height, thickness,
                    position_along_wall, left_edge, right_edge,
                    location_pt, facing_orient, hand_orient,
                    from_room, to_room, mark, comments, area
                ])
                
                successful_exports += 1
                
            except Exception as e:
                failed_exports += 1
                print("  ERROR processing opening {}: {}".format(opening_id, str(e)))
                import traceback
                traceback.print_exc()  # ADD THIS to see full error
                continue
    
    print("Openings exported successfully to: {}".format(OPENINGS_PATH))
    
except IOError as e:
    print("\nERROR: Cannot write to wall_openings.csv")
    print("Error: {}".format(str(e)))
    raise

# ========== EXPORT WALL MAPPING CSV ==========
print("\nExporting wall mapping...")
try:
    with codecs.open(MAPPING_PATH, mode="w", encoding="utf-8") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(["CombinedWallId", "OriginalWallId"])
        
        if basic_walls:
            combined_id = basic_walls[0].Id.IntegerValue
            for wall in basic_walls:
                csv_writer.writerow([combined_id, wall.Id.IntegerValue])
    
    print("Wall mapping exported successfully")
except Exception as e:
    print("Error exporting mapping: {}".format(str(e)))

# ========== SUMMARY ==========
print("\n" + "=" * 70)
print("FACADE EXPORT COMPLETE")
print("=" * 70)
print("Walls CSV: {}".format(WALLS_PATH))
print("Openings CSV: {}".format(OPENINGS_PATH))
print("Mapping CSV: {}".format(MAPPING_PATH))