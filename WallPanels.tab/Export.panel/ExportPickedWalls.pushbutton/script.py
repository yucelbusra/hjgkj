# -*- coding: utf-8 -*-
"""
FACADE-BY-FACADE EXPORT
Combines adjacent BASIC WALLS into a single facade sequence.
Openings (doors, windows) remain separate.
"""
from Autodesk.Revit.DB import (
    FilteredElementCollector, Wall, BuiltInParameter, LocationPoint, XYZ,
    Level, BuiltInCategory, ElementId
)
from pyrevit import revit, forms
import csv
import os
import codecs
import json

doc = revit.doc
uidoc = revit.uidoc

# ========== UI OUTPUT SELECTOR ==========
import clr
clr.AddReference('System.Windows.Forms')
from System.Windows.Forms import FolderBrowserDialog, DialogResult

dialog = FolderBrowserDialog()
dialog.Description = "Select Output Folder for Revit Walls"

initial_dir = os.path.join(os.path.expanduser("~"), "Desktop")
if not os.path.exists(initial_dir):
    initial_dir = os.path.expanduser("~")
dialog.SelectedPath = initial_dir

result = dialog.ShowDialog()
if result == DialogResult.OK:
    OUTPUT_DIR = dialog.SelectedPath
    print("Selected output folder: " + OUTPUT_DIR)
else:
    print("No folder selected. Exiting...")
    import sys
    sys.exit(0)

WALLS_FILE    = "walls.csv"
OPENINGS_FILE = "wall_openings.csv"
MAPPING_FILE  = "wall_mapping.csv"
WALLS_PATH    = os.path.join(OUTPUT_DIR, WALLS_FILE)
OPENINGS_PATH = os.path.join(OUTPUT_DIR, OPENINGS_FILE)
MAPPING_PATH  = os.path.join(OUTPUT_DIR, MAPPING_FILE)

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
    try: return getattr(BuiltInParameter, name)
    except: return None

def get_param(elem, key):
    if key is None: return None
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
        try: return p.AsInteger()
        except:
            try: return p.AsString() or ""
            except: return ""

def level_name(elem):
    try:
        lvl_id = elem.LevelId
        if lvl_id and lvl_id.IntegerValue > 0:
            lvl = doc.GetElement(lvl_id)
            return getattr(lvl, "Name", "")
    except: pass
    return (get_param_val(elem, get_bip("FAMILY_LEVEL_PARAM"), as_string=True)
            or get_param_val(elem, "Level", as_string=True)
            or "")

def get_sequential_wall_geometry(walls):
    """
    Compute the true visual-left → visual-right extent of a combined facade
    from the location curve endpoints of all selected wall segments.

    IMPORTANT — Location Line dependency:
      GetEndPoint() returns the LOCATION CURVE endpoint, which at a corner
      join is trimmed to the intersection of the two walls' reference lines.
      The reference line used depends on the wall's Location Line parameter:

        "Wall Center"     → endpoint is at the ADJACENT wall's center line
                            → inset from the outer corner by half the
                              adjacent wall's thickness
                            → leaves a visible gap at building corners

        "Finish Exterior" → endpoint is at the ADJACENT wall's exterior face
                            → sits at the outer corner of the building
                            → panels start flush with the facade face ✓

      RECOMMENDATION: set facade walls to "Finish Exterior" before exporting
      so that Start(X,Y,Z) captures the true outer face of the building.
    """
    if not walls: return None

    all_pts = []
    for w in walls:
        lc = w.Location.Curve
        all_pts.append(lc.GetEndPoint(0))
        all_pts.append(lc.GetEndPoint(1))

    try:
        normal = walls[0].Orientation
        up     = XYZ(0, 0, 1)
        visual_right_dir = normal.CrossProduct(up).Normalize()
    except Exception:
        visual_right_dir = XYZ(1, 0, 0)

    start_pt = min(all_pts, key=lambda p: p.DotProduct(visual_right_dir))
    end_pt   = max(all_pts, key=lambda p: p.DotProduct(visual_right_dir))

    min_z = min(p.Z for p in all_pts)
    max_z = min_z
    for w in walls:
        try:
            h   = w.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM).AsDouble()
            top = w.Location.Curve.GetEndPoint(0).Z + h
            if top > max_z: max_z = top
        except Exception: pass

    total_len = sum(w.Location.Curve.Length for w in walls)
    vec       = (end_pt - start_pt).Normalize()
    true_end  = start_pt + (vec * total_len)

    return {
        'start':     start_pt,
        'end':       true_end,
        'direction': vec,
        'height':    max_z - min_z,
        'min_z':     min_z
    }

# ========== GET SELECTED WALLS ==========
sel_ids = list(uidoc.Selection.GetElementIds())
if not sel_ids:
    forms.alert("Please select one or more walls before running this tool.", exitscript=True)

selected_walls    = []
selected_wall_ids = set()
for eid in sel_ids:
    elem = doc.GetElement(eid)
    if isinstance(elem, Wall):
        selected_walls.append(elem)
        selected_wall_ids.add(elem.Id.IntegerValue)

if not selected_walls:
    forms.alert("No walls found in the current selection.", exitscript=True)

print("Processing {} selected walls...".format(len(selected_walls)))

basic_walls = []
for wall in selected_walls:
    try:
        kind_name = str(wall.WallType.Kind)
        if kind_name.lower() == "basic":
            basic_walls.append(wall)
    except:
        basic_walls.append(wall)

print("  {} basic walls (will be combined into 1 wall layout)".format(len(basic_walls)))

# ========== COMPUTE COMBINED ID ONCE — shared by walls, openings, mapping ==========
# [FIX] Find the wall whose endpoint is closest to geo['start'] (the true visual-left
# of the combined facade).  This becomes the combined_id used throughout all three
# CSVs.  Using basic_walls[0] was wrong: it was the first wall in the selection list,
# not necessarily the leftmost one.  When the placement script calls
# get_wall_by_id(combined_id) and re-derives vis_left from that element, any offset
# between that wall's left endpoint and the true facade left shifted every panel.
combined_id      = basic_walls[0].Id.IntegerValue if basic_walls else None  # fallback
_facade_geo      = None   # computed below; reused in openings section

if basic_walls:
    _facade_geo  = get_sequential_wall_geometry(basic_walls)
    _tol         = 0.01   # ft  (~1/8 in)
    _found        = False
    for _cw in basic_walls:
        _lc = _cw.Location.Curve
        for _pt in [_lc.GetEndPoint(0), _lc.GetEndPoint(1)]:
            if _pt.DistanceTo(_facade_geo['start']) < _tol:
                combined_id = _cw.Id.IntegerValue
                _found = True
                print("  [FIX] combined_id -> {} (wall whose endpoint matches "
                      "geo['start'])".format(combined_id))
                break
        if _found: break
    if not _found:
        print("  [WARN] No wall endpoint within {:.3f} ft of geo['start'] -- "
              "using basic_walls[0] as combined_id.  Check wall joins and "
              "Location Line setting.".format(_tol))

# ========== EXPORT COMBINED BASIC WALLS CSV ==========
print("\nExporting combined basic walls to CSV...")

try:
    with codecs.open(WALLS_PATH, mode="w", encoding="utf-8") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow([
            "WallId","WallKind","TypeName","FamilyName","Function","IsStructural",
            "Width(ft)","Length(ft)","UnconnectedHeight(ft)","Area(sf)","Volume(cf)",
            "BaseLevel","BaseOffset(ft)","TopConstraint","TopOffset(ft)","LocationLine",
            "CurveType","Start(X,Y,Z)","End(X,Y,Z)","Mid(X,Y,Z)","CurveLength(ft)",
            "ArcRadius(ft)","ArcAngle(rad)","ArcCenter(X,Y,Z)","AxisDir(unit XYZ)","Normal(unit XYZ)",
            "Layers","WallCount","LevelElevations(in)"
        ])

        if not basic_walls:
            print("  No basic walls to export.")
        else:
            geo        = _facade_geo
            length_ft  = geo['start'].DistanceTo(geo['end'])
            height_ft  = geo['height']

            first_wall  = basic_walls[0]
            width_ft    = first_wall.Width
            wall_type   = doc.GetElement(first_wall.GetTypeId())
            kind_name   = "Basic (Combined)"
            type_name   = getattr(wall_type, "Name", "") or ""
            family_name = getattr(wall_type, "FamilyName", "") or ""

            function_str = (get_param_val(first_wall, "Function", as_string=True) or
                            get_param_val(first_wall, get_bip("WALL_ATTR_FUNCTION_PARAM"), as_string=True) or "")
            is_struct    = bool(getattr(first_wall, "Structural", False))
            area_sf      = rnum(length_ft * height_ft)
            vol_cf       = rnum(length_ft * height_ft * width_ft)
            base_lvl     = level_name(first_wall)
            base_off     = (get_param_val(first_wall, get_bip("WALL_BASE_OFFSET")) or
                            get_param_val(first_wall, "Base Offset") or "")
            top_con      = (get_param_val(first_wall, "Top Constraint", as_string=True) or
                            get_param_val(first_wall, get_bip("WALL_HEIGHT_TYPE"), as_string=True) or "")
            top_off      = (get_param_val(first_wall, get_bip("WALL_TOP_OFFSET")) or
                            get_param_val(first_wall, "Top Offset") or "")
            loc_line     = (get_param_val(first_wall, get_bip("WALL_KEY_REF_PARAM"), as_string=True) or
                            get_param_val(first_wall, "Location Line", as_string=True) or "")

            layers_info = []
            try:
                compound_structure = wall_type.GetCompoundStructure()
                if compound_structure:
                    for layer in compound_structure.GetLayers():
                        function_name = str(layer.Function)
                        material_id   = layer.MaterialId
                        material_name = "<By Category>"
                        if material_id.IntegerValue > 0:
                            material = doc.GetElement(material_id)
                            material_name = material.Name if material else "<By Category>"
                        thickness_in = round(layer.Width * 12, 3)
                        wraps        = "Yes" if layer.LayerCapFlag else "No"
                        layers_info.append("{} | {} | {} in | Wrap:{}".format(
                            function_name, material_name, thickness_in, wraps))
            except: pass
            layers_str = " || ".join(layers_info) if layers_info else "No Layers"

            levels         = list(FilteredElementCollector(doc).OfClass(Level))
            level_elev_in  = sorted([int(round(l.Elevation * 12)) for l in levels])
            mid_pt         = geo['start'] + (geo['direction'] * (length_ft / 2.0))

            csv_writer.writerow([
                combined_id, kind_name, type_name, family_name, function_str, is_struct,
                rnum(width_ft), rnum(length_ft), rnum(height_ft), area_sf, vol_cf,
                base_lvl, rnum(base_off), top_con, rnum(top_off), loc_line,
                "Line (Combined)", xyz_str(geo['start']), xyz_str(geo['end']), xyz_str(mid_pt), rnum(length_ft),
                "", "", "", "", "", layers_str, str(len(basic_walls)), json.dumps(level_elev_in)
            ])

            print("  Combined {} basic walls into single facade:".format(len(basic_walls)))
            print("    Length: {} ft | Height: {} ft".format(rnum(length_ft, 2), rnum(height_ft, 2)))
            print("    Start: {} | End: {}".format(xyz_str(geo['start']), xyz_str(geo['end'])))
            print("    combined_id: {} | LocationLine: {}".format(combined_id, loc_line))
            if "finish exterior" not in str(loc_line).lower():
                print("  [WARN] Location Line is '{}', not 'Finish Exterior'.".format(loc_line))
                print("         At building corners, GetEndPoint() will be inset from the outer")
                print("         corner by half the adjacent wall's thickness.  If panels should")
                print("         start at the outer corner face, change Location Line to")
                print("         'Finish Exterior' and re-export.")

    print("Walls exported successfully to: {}".format(WALLS_PATH))

except IOError as e:
    print("\nERROR: Cannot write to walls.csv - file may be open.")
    raise

# ========== COLLECT OPENINGS FROM SELECTED WALLS ==========
print("\nCollecting openings from selected walls...")

facade_wall_ids = set(selected_wall_ids)

def _hosted_on_facade(elem):
    try:
        host = elem.Host
        if host and host.Id.IntegerValue in facade_wall_ids: return True
    except Exception: pass
    try:
        host_id = elem.get_Parameter(BuiltInParameter.HOST_ID_PARAM)
        if host_id and host_id.AsElementId().IntegerValue in facade_wall_ids: return True
    except Exception: pass
    return False

doors   = [d for d in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors)
           .WhereElementIsNotElementType() if _hosted_on_facade(d)]
windows = [w for w in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Windows)
           .WhereElementIsNotElementType() if _hosted_on_facade(w)]

all_openings_list = list(doors) + list(windows)
print("  Total openings detected: {}".format(len(all_openings_list)))

# ========== EXPORT OPENINGS CSV ==========
print("\nExporting openings to CSV...")

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

        if basic_walls:
            geo = _facade_geo   # reuse — same geometry as walls section

            # [FIX] Use combined_id (leftmost wall, computed above) as HostWallId for
            # all openings.  Previously this used basic_walls[0].Id which could be a
            # different (non-leftmost) wall, creating a HostWallId mismatch between
            # the walls CSV and the openings CSV and causing panel_calculator to fail
            # to assign cutouts to their panels.
            _combined_wall = doc.GetElement(ElementId(combined_id))
            host_wall_type = (getattr(doc.GetElement(_combined_wall.GetTypeId()), "Name", "")
                              if _combined_wall else "")

            for opening in all_openings_list:
                try:
                    opening_id        = opening.Id.IntegerValue
                    category          = opening.Category.Name if opening.Category else ""
                    opening_type_elem = doc.GetElement(opening.GetTypeId())
                    type_name         = getattr(opening_type_elem, "Name", "") or ""
                    family_name       = (getattr(opening_type_elem, "FamilyName", "")
                                         if hasattr(opening_type_elem, "FamilyName") else "")

                    loc_pt = opening.Location.Point if hasattr(opening.Location, "Point") else XYZ(0,0,0)
                    dist_along = (loc_pt - geo['start']).DotProduct(geo['direction'])

                    def _get_dim(elem, bips, names):
                        sources = [elem]
                        try: sources.append(doc.GetElement(elem.GetTypeId()))
                        except Exception: pass
                        for src in sources:
                            if src is None: continue
                            for bip in bips:
                                try:
                                    p = src.get_Parameter(bip)
                                    if p:
                                        v = p.AsDouble()
                                        if v and v > 0: return v
                                except Exception: pass
                            for nm in names:
                                try:
                                    p = src.LookupParameter(nm)
                                    if p:
                                        v = p.AsDouble()
                                        if v and v > 0: return v
                                except Exception: pass
                        return 0.0

                    WIDTH_BIPS  = [BuiltInParameter.DOOR_WIDTH,  BuiltInParameter.WINDOW_WIDTH,
                                   BuiltInParameter.GENERIC_WIDTH, BuiltInParameter.FAMILY_WIDTH_PARAM]
                    HEIGHT_BIPS = [BuiltInParameter.DOOR_HEIGHT, BuiltInParameter.WINDOW_HEIGHT,
                                   BuiltInParameter.GENERIC_HEIGHT, BuiltInParameter.FAMILY_HEIGHT_PARAM]
                    WIDTH_NAMES  = ["Width","Rough Width","Nominal Width","Opening Width",
                                    "Frame Width","Clear Width","w","WIDTH"]
                    HEIGHT_NAMES = ["Height","Rough Height","Nominal Height","Opening Height",
                                    "Frame Height","Clear Height","Unconnected Height","h","HEIGHT"]

                    w_ft = _get_dim(opening, WIDTH_BIPS, WIDTH_NAMES)
                    h_ft = _get_dim(opening, HEIGHT_BIPS, HEIGHT_NAMES)

                    if w_ft <= 0 or h_ft <= 0:
                        print("  [WARN] Opening {} ({}): could not read W={} H={} -- "
                              "check family parameter names.".format(
                              opening.Id.IntegerValue,
                              getattr(doc.GetElement(opening.GetTypeId()), "Name", "?"),
                              w_ft, h_ft))
                    thk_ft        = get_param_val(opening, "Thickness")
                    left_edge_ft  = dist_along - (w_ft / 2.0)
                    right_edge_ft = dist_along + (w_ft / 2.0)
                    sill_height_ft = loc_pt.Z - geo['min_z']

                    lvl           = level_name(opening)
                    facing_orient = xyz_str(opening.FacingOrientation) if hasattr(opening, "FacingOrientation") else ""
                    hand_orient   = xyz_str(opening.HandOrientation)   if hasattr(opening, "HandOrientation")   else ""
                    from_room     = get_param_val(opening, "From Room", as_string=True)
                    to_room       = get_param_val(opening, "To Room",   as_string=True)
                    mark          = get_param_val(opening, "Mark",      as_string=True)
                    comments      = get_param_val(opening, "Comments",  as_string=True)
                    area_sf       = w_ft * h_ft

                    csv_writer.writerow([
                        opening_id, category, category, type_name, family_name,
                        combined_id, host_wall_type, lvl, rnum(sill_height_ft),
                        rnum(w_ft), rnum(h_ft), rnum(thk_ft),
                        rnum(dist_along), rnum(left_edge_ft), rnum(right_edge_ft),
                        xyz_str(loc_pt), facing_orient, hand_orient,
                        from_room, to_room, mark, comments, rnum(area_sf)
                    ])
                except Exception as ex:
                    print("  Failed to export opening {}: {}".format(opening.Id.IntegerValue, ex))
                    continue

    print("Openings exported successfully to: {}".format(OPENINGS_PATH))

except IOError as e:
    print("\nERROR: Cannot write to wall_openings.csv")
    raise

# ========== EXPORT WALL MAPPING CSV ==========
print("\nExporting wall mapping...")
try:
    with codecs.open(MAPPING_PATH, mode="w", encoding="utf-8") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(["CombinedWallId", "OriginalWallId"])
        if basic_walls:
            # [FIX] Use combined_id (leftmost wall) — was basic_walls[0].Id which could
            # be a different wall, making the mapping inconsistent with the walls CSV.
            for wall in basic_walls:
                csv_writer.writerow([combined_id, wall.Id.IntegerValue])
    print("Wall mapping exported successfully.\n")
except Exception as e:
    print("Error exporting mapping: {}".format(e))

# ========== SUMMARY ==========
print("\n" + "=" * 70)
print("FACADE EXPORT COMPLETE")
print("=" * 70)
print("Walls CSV:    {}".format(WALLS_PATH))
print("Openings CSV: {}".format(OPENINGS_PATH))
print("Mapping CSV:  {}".format(MAPPING_PATH))