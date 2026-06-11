# coding: ascii
"""
UPDATE PANEL CSV - Sync Revit panel instances back to optimized_panel_placement.csv

Reads all placed panel family instances from the model, extracts their
current position, size, and void parameters, and writes an updated
optimized_panel_placement.csv reflecting any manual changes made since
the original placement run.

"""

from Autodesk.Revit.DB import (
    FilteredElementCollector, Wall, FamilyInstance,
    BuiltInCategory, BuiltInParameter, ElementId, XYZ
)

from pyrevit import revit, forms
import os
import csv
import json
import math

import clr
clr.AddReference('System.Windows.Forms')
from System.Windows.Forms import OpenFileDialog, DialogResult

doc   = revit.doc
uidoc = revit.uidoc

CSV_FILENAME = "optimized_panel_placement.csv"

PANEL_FAMILY_NAMES = [
    "RNGD_Optimizer Ext Wall Panel_Opening",
    "RNGD_Optimizer Ext Wall Panel",
]

CSV_COLUMNS = [
    "wall_id", "panel_name", "x_in", "y_in",
    "width_in", "height_in", "x_ref", "rotation_deg", "cutouts_json"
]


# ========== UTILITIES ==========

def _feet(v):  return float(v) / 12.0
def _inches(v): return float(v) * 12.0
def _safe_float(v, d=0.0):
    try: return float(v)
    except: return d

def _timestamp():
    import System
    n = System.DateTime.Now
    return "{0:04d}{1:02d}{2:02d}_{3:02d}{4:02d}{5:02d}".format(
        n.Year, n.Month, n.Day, n.Hour, n.Minute, n.Second)

def _pick_csv():
    """Ask user to locate the existing CSV file."""
    try:
        ofd = OpenFileDialog()
        ofd.Title = "Locate optimized_panel_placement.csv to update"
        ofd.Filter = "CSV files (*.csv)|*.csv|All files (*.*)|*.*"
        ofd.FileName = CSV_FILENAME
        result = ofd.ShowDialog()
        if result == DialogResult.OK:
            return str(ofd.FileName)
    except:
        pass
    return None


# ========== PARAMETER HELPERS ==========

def _resolve_param(inst, param_name):
    variants = [param_name]
    if param_name.endswith(" (default)"):
        variants.append(param_name[:-len(" (default)")])
    else:
        variants.append(param_name + " (default)")
    for name in variants:
        p = inst.LookupParameter(name)
        if p is not None:
            return p
        try:
            p = inst.Symbol.LookupParameter(name)
            if p is not None:
                return p
        except:
            pass
    return None

def _get_inches(inst, name):
    p = _resolve_param(inst, name)
    if p is None: return None
    try: return _inches(p.AsDouble())
    except: return None

def _get_int(inst, name):
    p = _resolve_param(inst, name)
    if p is None: return None
    try: return p.AsInteger()
    except: return None

def _get_str(inst, name):
    p = _resolve_param(inst, name)
    if p is None: return None
    try: return p.AsString() or ""
    except: return None


# ========== WALL GEOMETRY ==========

def _get_wall_direction(wall):
    """Return (origin_point, unit_direction) from curve start to end."""
    lc = wall.Location.Curve
    p0 = lc.GetEndPoint(0)
    p1 = lc.GetEndPoint(1)
    direction = (p1 - p0).Normalize()
    return p0, direction

def _get_wall_base_elevation(wall):
    base_z = 0.0
    try:
        lvl_id = wall.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT).AsElementId()
        if lvl_id and lvl_id.IntegerValue > 0:
            level = doc.GetElement(lvl_id)
            if level: base_z = level.Elevation
    except: pass
    try:
        bo = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
        if bo: base_z += bo.AsDouble()
    except: pass
    return base_z


# ========== PANEL DETECTION ==========

def _is_panel_family(inst):
    try:
        if any(inst.Symbol.Family.Name == n for n in PANEL_FAMILY_NAMES):
            return True
        for cand in ["Overall Width", "Overall Width (default)"]:
            if inst.LookupParameter(cand) is not None:
                return True
        return False
    except: return False

def _panel_near_wall(inst, wall, vis_left, wall_dir, wall_base_z):
    try:
        loc_pt = inst.Location.Point
        wall_len_in   = _inches(wall.Location.Curve.Length)
        wall_thick_in = _inches(wall.Width)
        wall_normal   = wall.Orientation
        vec = loc_pt - vis_left
        x_in = _inches(vec.DotProduct(wall_dir))
        if x_in < -12.0 or x_in > wall_len_in + 12.0: return False
        if _inches(abs(vec.DotProduct(wall_normal))) > wall_thick_in + 12.0: return False
        if loc_pt.Z < (wall_base_z - 1.0): return False
        return True
    except: return False

def _get_rotation_deg(inst):
    try:
        facing = inst.FacingOrientation
        return math.degrees(math.atan2(facing.Y, facing.X))
    except: return 0.0


# ========== VOID EXTRACTION ==========

def _extract_cutouts(inst, existing_cutouts=None):
    """
    Read current void parameters from the instance.
    Merges with existing_cutouts to preserve the 'type' field
    (which is not stored in family params).
    Returns list of cutout dicts.
    """
    void_defs = [
        {"vis": "VOID 1", "width": "UNIT 1 WIDTH",  "height": "UNIT 1 HEIGHT",
         "x": "Void 1 X Offset", "y": "Void 1 Y Offset",
         "jamb": "VOID 1 JAMB CLR", "head": "VOID 1 HEAD CLR", "sill": "VOID 1 SILL CLR"},
        {"vis": "VOID 2", "width": "UNIT 2 WIDTH",  "height": "UNIT 2 HEIGHT",
         "x": "Void 2 X Offset", "y": "Void 2 Y Offset",
         "jamb": "VOID 2 JAMB CLR", "head": "VOID 2 HEAD CLR", "sill": "VOID 2 SILL CLR"},
    ]

    cutouts = []
    for idx, vd in enumerate(void_defs):
        vis = _get_int(inst, vd["vis"])
        if vis is None or vis == 0:
            continue

        width_in  = _get_inches(inst, vd["width"])
        height_in = _get_inches(inst, vd["height"])
        if width_in is None or height_in is None: continue
        if width_in < 1.0 or height_in < 1.0: continue  # skip hidden tiny voids

        x_in   = _get_inches(inst, vd["x"])
        y_in   = _get_inches(inst, vd["y"])
        jamb   = _get_inches(inst, vd["jamb"])
        head   = _get_inches(inst, vd["head"])
        sill   = _get_inches(inst, vd["sill"])

        # Preserve opening type from existing CSV if available
        opening_type = "unknown"
        if existing_cutouts and idx < len(existing_cutouts):
            opening_type = existing_cutouts[idx].get("type", "unknown")

        cutouts.append({
            "width_in":    round(width_in,  4),
            "height_in":   round(height_in, 4),
            "x_in":        round(x_in,      4) if x_in   is not None else 0.0,
            "y_in":        round(y_in,      4) if y_in   is not None else 0.0,
            "jamb_clr_in": round(jamb,      4) if jamb   is not None else 0.0,
            "head_clr_in": round(head,      4) if head   is not None else 0.0,
            "sill_clr_in": round(sill,      4) if sill   is not None else 0.0,
            "type":        opening_type,
        })

    return cutouts


# ========== EXISTING CSV LOAD ==========

def load_existing_csv(csv_path):
    existing = {}
    if not os.path.exists(csv_path):
        return existing
    try:
        with open(csv_path, "rb") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("panel_name", "").strip()
                if not name:
                    continue
                try:
                    cutouts = json.loads(row.get("cutouts_json", "[]"))
                except:
                    cutouts = []
                existing[name] = {
                    "x_ref": row.get("x_ref", "start"),
                    "cutouts": cutouts,
                }
        print("[CSV] Loaded existing data for {0} panels".format(len(existing)))
    except Exception as e:
        print("[CSV] Could not read existing CSV: {0}".format(e))
    return existing


# ========== MAIN EXTRACTION ==========

def extract_all_panels(existing):
    """
    Walk all walls, find all panel instances, extract current state.
    Merges with existing CSV data to preserve non-recoverable fields.
    """
    rows = []
    walls = FilteredElementCollector(doc).OfClass(Wall).ToElements()
    panel_count = 0
    claimed_ids = set()  # prevents same instance being claimed by two walls

    for wall in walls:
        wall_id     = str(wall.Id.IntegerValue)
        vis_left, wall_dir = _get_wall_direction(wall)
        wall_base_z = _get_wall_base_elevation(wall)
        wall_len_in   = _inches(wall.Location.Curve.Length) if wall.Location else 9999.0
        wall_thick_in = _inches(wall.Width)
        wall_normal   = wall.Orientation

        wall_panels = []
        for inst in FilteredElementCollector(doc).OfClass(FamilyInstance).ToElements():
            try:
                if inst.Id.IntegerValue in claimed_ids: continue  # skip already-claimed
                if not _is_panel_family(inst): continue
                try:
                    host = inst.Host
                    if host is not None and host.Id == wall.Id:
                        wall_panels.append(inst)
                        continue
                except: pass
                # proximity check for non-hosted
                loc_pt = inst.Location.Point
                vec = loc_pt - vis_left
                x_in = _inches(vec.DotProduct(wall_dir))
                if x_in < -12.0 or x_in > wall_len_in + 12.0: continue
                if _inches(abs(vec.DotProduct(wall_normal))) > wall_thick_in + 12.0: continue
                if loc_pt.Z < (wall_base_z - 1.0): continue
                wall_panels.append(inst)
            except: continue

        if not wall_panels:
            continue

        # Claim all panels for this wall before any other wall can grab them
        for inst in wall_panels:
            claimed_ids.add(inst.Id.IntegerValue)

        # ── FLIP ORIGIN IF PANELS FACE OPPOSITE TO wall_dir ──────────────────
        # Panels placed at rotation=180 mean the wall curve runs right-to-left
        # relative to how the original placement script measured x. Detect this
        # by checking the first panel's FacingOrientation against wall_dir and
        # flip both the origin and direction so x=0 always matches the original
        # left edge of the panel layout.
        try:
            sample_facing = wall_panels[0].FacingOrientation
            if sample_facing.DotProduct(wall_dir) < 0:
                lc     = wall.Location.Curve
                p0     = lc.GetEndPoint(0)
                p1     = lc.GetEndPoint(1)
                vis_left = p1                      # swap origin to far end
                wall_dir = (p0 - p1).Normalize()   # reverse direction
        except:
            pass
        # ─────────────────────────────────────────────────────────────────────

        def sort_key(inst):
            try:
                vec = inst.Location.Point - vis_left
                return _inches(vec.DotProduct(wall_dir))
            except: return 0.0

        wall_panels.sort(key=sort_key)

        for inst in wall_panels:
            try:
                loc_pt = inst.Location.Point
                vec    = loc_pt - vis_left
                x_in   = _inches(vec.DotProduct(wall_dir))
                y_in   = _inches(loc_pt.Z - wall_base_z)
            except: continue

            width_in  = _get_inches(inst, "Overall Width")
            height_in = _get_inches(inst, "Overall Height")
            if width_in is None or height_in is None: continue

            mark = _get_str(inst, "Mark") or str(inst.Id.IntegerValue)
            rot  = _get_rotation_deg(inst)

            # ── COORDINATE CORRECTION FOR ROTATED PANELS ─────────────────────
            # Panels are placed rotated 90° so their local X axis (width) runs
            # vertically in the wall plane. Revit stores the instance origin at
            # the panel's insertion point, which after a standard +90° rotation
            # sits at the panel's LEFT edge — matching the CSV convention where
            # x_in = distance from wall origin (left corner) to panel left edge.
            #
            # If the panel was placed at -90° (i.e. 270°), the insertion point
            # lands at the panel's RIGHT edge instead. We subtract width_in to
            # convert back to a left-edge coordinate so the CSV is consistent
            # regardless of which rotation direction was used during placement.
            #
            # Rotation is normalised to [0, 360) before the check.
            rot_norm = rot % 360.0
            if 180.0 < rot_norm <= 360.0:   # placed "flipped" (-90 / 270 deg)
                x_in -= width_in
            # ─────────────────────────────────────────────────────────────────

            # Merge with existing CSV row
            prev         = existing.get(mark, {})
            x_ref        = prev.get("x_ref", "start")
            prev_cutouts = prev.get("cutouts", [])
            cutouts      = _extract_cutouts(inst, prev_cutouts)

            rows.append({
                "wall_id":      wall_id,
                "panel_name":   mark,
                "x_in":         round(x_in,      4),  # left-edge from wall origin
                "y_in":         round(y_in,      4),
                "width_in":     round(width_in,  4),
                "height_in":    round(height_in, 4),
                "x_ref":        x_ref,
                "rotation_deg": 90.0,  # canonical; coord already corrected above
                "cutouts_json": json.dumps(cutouts),
            })
            panel_count += 1

    print("[EXTRACT] {0} panels across {1} walls".format(panel_count, len(list(walls))))
    return rows


# ========== CSV WRITE ==========

def backup_and_write(csv_path, rows):
    if os.path.exists(csv_path):
        backup_name = os.path.join(
            os.path.dirname(csv_path),
            "optimized_panel_placement_backup_{0}.csv".format(_timestamp())
        )
        import shutil
        shutil.copy(csv_path, backup_name)
        print("[BACKUP] {0}".format(backup_name))

    # IronPython 2.x fix: io.open supports newline= unlike built-in open()
    import io
    with io.open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            out_row = {}
            for col in CSV_COLUMNS:
                val = row.get(col, "")
                if val is None:
                    val = ""
                out_row[col] = str(val) if not isinstance(val, str) else val
            writer.writerow(out_row)

    print("[WRITE] {0} rows -> {1}".format(len(rows), csv_path))


# ========== MAIN ==========

def main():
    print("--- UPDATE PANEL CSV ---")

    # 1. Locate the CSV to update
    csv_path = _pick_csv()
    if not csv_path:
        print("Cancelled.")
        return

    # 2. Load existing CSV for smart merge
    print("[STEP 1] Reading existing CSV...")
    existing = load_existing_csv(csv_path)

    # 3. Extract current panel state from Revit
    print("[STEP 2] Extracting panel data from Revit...")
    rows = extract_all_panels(existing)

    if not rows:
        forms.alert(
            "No panel instances found in the model. "
            "Make sure panels are placed.",
            title="Update Panel CSV"
        )
        return

    # 4. Confirm
    wall_ids = list(set(r["wall_id"] for r in rows))
    res = forms.alert(
        "Found {0} panels across {1} wall(s). "
        "This will overwrite {2} with a backup created first. Proceed?".format(
            len(rows), len(wall_ids), os.path.basename(csv_path)),
        title="Update Panel CSV",
        yes=True, no=True
    )
    if not res:
        print("Cancelled.")
        return

    # 5. Write
    print("[STEP 3] Writing updated CSV...")
    backup_and_write(csv_path, rows)

    print("\n--- DONE ---")
    print("Updated:        {0}".format(csv_path))
    print("Panels written: {0}".format(len(rows)))
    print("Walls covered:  {0}".format(len(wall_ids)))

    forms.alert(
        "CSV updated: {0} panels across {1} wall(s).".format(
            len(rows), len(wall_ids)),
        title="Update Panel CSV"
    )


if __name__ == "__main__":
    main()