# -*- coding: utf-8 -*-
"""
Places largest possible wall panels on walls defined in walls.csv,
considering openings from wall_openings.csv.

All user inputs are collected in a single Revit-style parameter grid dialog.
"""

from __future__ import print_function
import os
from datetime import datetime

# --- .NET UI imports ---
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from System.Windows.Forms import (
    Application, FolderBrowserDialog, DialogResult, Form,
    Label, RadioButton, Button, FormBorderStyle, FormStartPosition,
    MessageBox, MessageBoxButtons, MessageBoxIcon, TextBox,
    Panel, ScrollableControl, AnchorStyles, DockStyle, Padding,
    FlatStyle
)
from System.Drawing import (
    Point, Size, Color, Font, FontStyle, ContentAlignment, GraphicsUnit,
    SolidBrush, Pen, StringFormat, StringAlignment, RectangleF, PointF
)
from System import IntPtr
import System.Windows.Forms as _WF
IWin32Window = _WF.IWin32Window
from System.Drawing import Graphics as _Graphics
from System.Windows.Forms import Screen as _Screen


# =============================================================================
# DPI / SCREEN-SCALE HELPERS
# =============================================================================
def _get_dpi_scale():
    """
    Return a float scale factor relative to 96 DPI (Windows standard).
    On a 96-DPI (100 %) screen  -> 1.0
    On a 120-DPI (125 %) screen -> 1.25
    On a 144-DPI (150 %) screen -> 1.5
    Falls back to 1.0 on any error (IronPython / Revit environment).
    """
    try:
        g = _Graphics.FromHwnd(IntPtr.Zero)
        dpi = g.DpiX
        g.Dispose()
        return max(1.0, float(dpi) / 96.0)
    except Exception:
        return 1.0

def _get_screen_size():
    """Return (width, height) of the primary screen in pixels."""
    try:
        b = _Screen.PrimaryScreen.Bounds
        return (b.Width, b.Height)
    except Exception:
        return (1920, 1080)

def _scale(value, factor):
    """Scale an integer pixel value by factor, always returning an int."""
    return int(round(value * factor))

# --- Additional refs to bind dialogs to Revit main window ---
try:
    clr.AddReference('RevitAPI')
    clr.AddReference('RevitAPIUI')
    from Autodesk.Revit.UI import UIApplication
except Exception:
    UIApplication = None  # Running outside Revit context

# --- Import optimizer module sitting next to this script ---
import sys as _sys
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in _sys.path:
    _sys.path.insert(0, _this_dir)
# Force reload so edits to panel_calculator.py are always picked up
if 'panel_calculator' in _sys.modules:
    del _sys.modules['panel_calculator']
try:
    import panel_calculator as opt
except Exception as e:
    raise Exception("Failed to import panel_calculator.py from {0}: {1}".format(_this_dir, e))


# =========================
# Helpers: Revit window owner
# =========================
class WindowWrapper(IWin32Window):
    def __init__(self, handle):
        self._hwnd = handle
    @property
    def Handle(self):
        return self._hwnd

def get_revit_owner():
    try:
        uiapp = __revit__
        hwnd = uiapp.MainWindowHandle
        return WindowWrapper(IntPtr(hwnd))
    except Exception as e:
        print("Warning: could not retrieve Revit main window handle: {0}".format(e))
        return None


# =========================
# UI: Folder Picker
# =========================
def pick_data_folder():
    owner = get_revit_owner()
    dialog = FolderBrowserDialog()
    dialog.Description = "Select folder containing walls.csv and wall_openings.csv"
    initial_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.exists(initial_dir):
        initial_dir = os.path.expanduser("~")
    dialog.SelectedPath = initial_dir
    result = dialog.ShowDialog(owner) if owner else dialog.ShowDialog()
    if result == DialogResult.OK and dialog.SelectedPath and os.path.isdir(dialog.SelectedPath):
        return dialog.SelectedPath
    else:
        return os.path.dirname(os.path.abspath(__file__))


# =============================================================================
# HELPERS
# =============================================================================
def _safe_float(text, default):
    try:
        return float(str(text).strip())
    except Exception:
        return default

# Visual constants matching Revit's Family Types palette
CLR_TITLE_BG   = Color.FromArgb(51,  51,  51)
CLR_GROUP_BG   = Color.FromArgb(214, 222, 236)
CLR_GROUP_FG   = Color.FromArgb(20,  20,  20)
CLR_SUB_BG     = Color.FromArgb(235, 239, 247)
CLR_ROW_BG     = Color.White
CLR_ROW_ALT    = Color.FromArgb(248, 249, 252)
CLR_ACCENT     = Color.FromArgb(0,   114, 198)
CLR_BODY_BG    = Color.FromArgb(240, 240, 240)

from System.Drawing import GraphicsUnit

# Compute once at import time so every dialog shares the same factor
_DPI_SCALE = _get_dpi_scale()

def _make_fonts(scale):
    """Build the four font objects scaled to the current DPI."""
    base  = max(8.0,  9.0  * scale)
    title = max(9.0,  10.0 * scale)
    small = max(7.0,  8.0  * scale)
    return (
        Font("Segoe UI", base,  FontStyle.Regular, GraphicsUnit.Point),
        Font("Segoe UI", base,  FontStyle.Bold,    GraphicsUnit.Point),
        Font("Segoe UI", title, FontStyle.Bold,    GraphicsUnit.Point),
        Font("Segoe UI", small, FontStyle.Regular, GraphicsUnit.Point),
    )

FNT_NORMAL, FNT_BOLD, FNT_TITLE, FNT_SMALL = _make_fonts(_DPI_SCALE)

ROW_H   = _scale(26,  _DPI_SCALE)
LBL_W   = _scale(200, _DPI_SCALE)
TXT_W   = _scale(110, _DPI_SCALE)
INDENT  = 0     # rows dock to fill, no manual indent needed


# =============================================================================
# UNIFIED CONFIG DIALOG
# =============================================================================
class ConfigDialog(Form):
    """Single Revit-style parameter grid - all config in one scrollable window."""

    # Column geometry — computed at instance creation from DPI scale.
    # Class-level values are fallback only; _init_scaled_geometry() overwrites them.
    _LEFT   = 4
    _LBL_W  = 210
    _TXT_X  = 222
    _TXT_W  = 115
    _CLR1_X = 222
    _CLR2_X = 352
    _TOT_X  = 482
    _TOT_W  = 75
    _ROW_H  = 26

    def __init__(self, config):
        self._cfg = config
        self._init_scaled_geometry()
        self._build()

    def _init_scaled_geometry(self):
        """Compute all pixel constants relative to the current DPI scale."""
        s = _DPI_SCALE
        # Left inset
        self._LEFT   = _scale(4,   s)
        # Label column width
        self._LBL_W  = _scale(210, s)
        # Textbox x for single-value rows (left + lbl + 8px gap)
        self._TXT_X  = _scale(222, s)
        # Textbox width (numeric)
        self._TXT_W  = _scale(115, s)
        # Clearance column x positions
        self._CLR1_X = _scale(222, s)   # Rough Opening textbox
        self._CLR2_X = _scale(352, s)   # Panel Clearance textbox
        self._TOT_X  = _scale(482, s)   # Total label
        self._TOT_W  = _scale(75,  s)   # Total label width
        # Row height
        self._ROW_H  = _scale(26,  s)
        # Diagram panel width
        self._DIAG_W = _scale(280, s)

    # _DIAG_W is set per-instance in _init_scaled_geometry()

    def _build(self):
        s = _DPI_SCALE
        self.Text            = "Panel Optimizer - Configuration"
        self.StartPosition   = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize     = Size(_scale(900, s), _scale(580, s))
        # Size the form to 80 % of screen real-estate, capped at a comfortable maximum
        sw, sh = _get_screen_size()
        form_w = min(_scale(1020, s), int(sw * 0.82))
        form_h = min(_scale(820,  s), int(sh * 0.82))
        self.ClientSize      = Size(form_w, form_h)
        self.TopMost         = True
        self.BackColor       = CLR_BODY_BG
        self.Font            = FNT_NORMAL

        # --- Title strip (top, fixed height) ---
        title_h = _scale(38, s)
        tp = Panel()
        tp.Dock      = DockStyle.Top
        tp.Height    = title_h
        tp.BackColor = CLR_TITLE_BG
        tl = Label()
        tl.Text      = "Panel Optimizer - Configuration"
        tl.Font      = FNT_TITLE
        tl.ForeColor = Color.White
        tl.AutoSize  = True
        tl.Location  = Point(_scale(12, s), _scale(10, s))
        tp.Controls.Add(tl)
        self.Controls.Add(tp)

        # --- Button bar (bottom, fixed height, always visible) ---
        btn_bar_h  = _scale(48, s)
        btn_w_ok   = _scale(120, s)
        btn_w_can  = _scale(90,  s)
        btn_h      = _scale(30,  s)
        bb = Panel()
        bb.Dock      = DockStyle.Bottom
        bb.Height    = btn_bar_h
        bb.BackColor = Color.FromArgb(220, 220, 220)

        self._btnOK = Button()
        self._btnOK.Text      = "Run Optimizer"
        self._btnOK.Size      = Size(btn_w_ok, btn_h)
        self._btnOK.Anchor    = AnchorStyles.Top | AnchorStyles.Right
        self._btnOK.BackColor = CLR_ACCENT
        self._btnOK.ForeColor = Color.White
        self._btnOK.FlatStyle = FlatStyle.Flat
        self._btnOK.Font      = FNT_BOLD
        self._btnOK.Click    += self._on_ok
        self.AcceptButton     = self._btnOK

        self._btnCancel = Button()
        self._btnCancel.Text         = "Cancel"
        self._btnCancel.Size         = Size(btn_w_can, btn_h)
        self._btnCancel.Anchor       = AnchorStyles.Top | AnchorStyles.Right
        self._btnCancel.DialogResult = DialogResult.Cancel
        self._btnCancel.FlatStyle    = FlatStyle.Flat
        self.CancelButton            = self._btnCancel

        bb.Controls.Add(self._btnOK)
        bb.Controls.Add(self._btnCancel)
        self.Controls.Add(bb)

        # Position buttons - must happen after bb is added
        bb.SizeChanged += self._reposition_buttons
        self._bb = bb

        # --- Diagram panel (right side, fixed width) ---
        self._diag = Panel()
        self._diag.Dock      = DockStyle.Right
        self._diag.Width     = self._DIAG_W
        self._diag.BackColor = Color.FromArgb(250, 251, 254)
        self._diag.Paint    += self._draw_diagram
        self.Controls.Add(self._diag)

        # Thin separator line between scroll and diagram
        sep = Panel()
        sep.Dock      = DockStyle.Right
        sep.Width     = 1
        sep.BackColor = Color.FromArgb(190, 200, 215)
        self.Controls.Add(sep)

        # --- Scrollable body (fills remaining space) ---
        self._scroll = ScrollableControl()
        self._scroll.Dock       = DockStyle.Fill
        self._scroll.AutoScroll = True
        self._scroll.BackColor  = CLR_BODY_BG
        self.Controls.Add(self._scroll)

        self._populate_rows()

    def _draw_diagram(self, sender, e):
        """
        Draws a single panel with a cutout opening, showing:
        - Rough opening clearance  (gap between opening edge and rough frame)
        - Panel clearance          (gap from rough frame to panel edge)
        Styled like the reference sketch: clean line-art on white.
        """
        from System.Drawing import Drawing2D
        g = e.Graphics
        g.SmoothingMode = Drawing2D.SmoothingMode.AntiAlias
        # TextRenderingHint not available in IronPython Drawing2D

        W = sender.ClientSize.Width
        H = sender.ClientSize.Height

        # ---- colours / pens ----
        c_bg      = Color.White
        c_panel   = Color.FromArgb(248, 248, 248)
        c_border  = Color.FromArgb(80,  80,  80)
        c_dim     = Color.FromArgb(60,  60,  60)
        c_opening = Color.White
        c_ro_fill = Color.FromArgb(230, 240, 255)   # rough opening zone tint
        c_cp_fill = Color.FromArgb(255, 240, 220)   # clearance-to-panel zone tint

        pen_panel  = Pen(c_border, 1.5)
        pen_dim    = Pen(c_dim,    1.0)
        pen_thin   = Pen(Color.FromArgb(160, 160, 160), 0.75)

        fnt_lbl  = Font("Segoe UI", 7.5, FontStyle.Regular, GraphicsUnit.Point)
        fnt_bold = Font("Segoe UI", 7.5, FontStyle.Bold,    GraphicsUnit.Point)
        fnt_ttl  = Font("Segoe UI", 8.5, FontStyle.Bold,    GraphicsUnit.Point)

        sf_c = StringFormat()
        sf_c.Alignment     = StringAlignment.Center
        sf_c.LineAlignment = StringAlignment.Center

        sf_l = StringFormat()
        sf_l.Alignment     = StringAlignment.Near
        sf_l.LineAlignment = StringAlignment.Center

        brush_dim   = SolidBrush(c_dim)
        brush_open  = SolidBrush(Color.FromArgb(120, 130, 145))
        brush_white = SolidBrush(Color.White)

        # ---- layout ----
        # Panel occupies most of the diagram
        pad    = 30          # outer margin
        ttl_h  = 28          # space for title at top
        pnl_x  = pad
        pnl_y  = pad + ttl_h
        pnl_w  = W - pad * 2
        pnl_h  = H - pnl_y - pad - 20

        # Clearance zone thicknesses (pixels)
        ro_px  = 10   # rough opening clearance thickness
        cp_px  = 18   # clearance to panel thickness

        # Opening void sits centred in the panel
        op_margin_x = cp_px + ro_px + 30   # left/right space
        op_margin_y = cp_px + ro_px + 20   # top/bottom space
        op_x = pnl_x + op_margin_x
        op_y = pnl_y + op_margin_y
        op_w = pnl_w - op_margin_x * 2
        op_h = pnl_h - op_margin_y * 2

        # Rough opening frame (sits between opening and clearance-to-panel zone)
        rf_x = op_x - ro_px
        rf_y = op_y - ro_px
        rf_w = op_w + ro_px * 2
        rf_h = op_h + ro_px * 2

        # ---- draw background ----
        with SolidBrush(c_bg) as b:
            g.FillRectangle(b, 0, 0, W, H)

        # ---- title ----
        with SolidBrush(Color.FromArgb(50, 50, 50)) as b:
            rf = RectangleF(float(pnl_x), 6.0, float(pnl_w), float(ttl_h))
            g.DrawString("Clearance Reference", fnt_ttl, b, rf, sf_c)

        # ---- panel fill ----
        with SolidBrush(c_panel) as b:
            g.FillRectangle(b, pnl_x, pnl_y, pnl_w, pnl_h)

        # ---- clearance-to-panel zones (coloured) ----
        # Left strip
        with SolidBrush(c_cp_fill) as b:
            g.FillRectangle(b, pnl_x,            rf_y, cp_px, rf_h)
        # Right strip
        with SolidBrush(c_cp_fill) as b:
            g.FillRectangle(b, rf_x + rf_w,       rf_y, cp_px, rf_h)
        # Top strip
        with SolidBrush(c_cp_fill) as b:
            g.FillRectangle(b, rf_x - cp_px,      pnl_y, rf_w + cp_px*2, cp_px)
        # Bottom strip
        with SolidBrush(c_cp_fill) as b:
            g.FillRectangle(b, rf_x - cp_px,      rf_y + rf_h, rf_w + cp_px*2, cp_px)

        # ---- rough opening zones (different colour) ----
        with SolidBrush(c_ro_fill) as b:
            g.FillRectangle(b, rf_x, rf_y, ro_px, rf_h)          # left
            g.FillRectangle(b, op_x + op_w, rf_y, ro_px, rf_h)   # right
            g.FillRectangle(b, rf_x, rf_y, rf_w, ro_px)          # top
            g.FillRectangle(b, rf_x, op_y + op_h, rf_w, ro_px)   # bottom

        # ---- opening void ----
        with SolidBrush(c_opening) as b:
            g.FillRectangle(b, op_x, op_y, op_w, op_h)

        # ---- panel border ----
        g.DrawRectangle(pen_panel, pnl_x, pnl_y, pnl_w, pnl_h)

        # ---- rough opening frame border ----
        g.DrawRectangle(pen_thin, rf_x, rf_y, rf_w, rf_h)

        # ---- opening border ----
        g.DrawRectangle(pen_thin, op_x, op_y, op_w, op_h)

        # ---- opening label ----
        with SolidBrush(brush_open.Color) as b:
            rf2 = RectangleF(float(op_x), float(op_y), float(op_w), float(op_h))
            g.DrawString("Opening", fnt_bold, b, rf2, sf_c)

        # ---- dimension arrows ----
        # Helper: draw bracket/arrow line with label
        def h_arrow(x1, x2, ay, label, above=True):
            """Horizontal dimension line with tick marks and label."""
            if abs(x2 - x1) < 3:
                return
            # line
            g.DrawLine(pen_dim, x1, ay, x2, ay)
            # ticks
            g.DrawLine(pen_dim, x1, ay - 4, x1, ay + 4)
            g.DrawLine(pen_dim, x2, ay - 4, x2, ay + 4)
            # label
            lh = 13.0
            ly = float(ay - lh - 1) if above else float(ay + 2)
            g.DrawString(label, fnt_lbl, brush_dim,
                         RectangleF(float(x1), ly, float(x2 - x1), lh), sf_c)

        def v_arrow(vx, y1, y2, label, right=True):
            """Vertical dimension line with tick marks and label to the side."""
            if abs(y2 - y1) < 3:
                return
            g.DrawLine(pen_dim, vx, y1, vx, y2)
            g.DrawLine(pen_dim, vx - 4, y1, vx + 4, y1)
            g.DrawLine(pen_dim, vx - 4, y2, vx + 4, y2)
            mid = (y1 + y2) / 2.0
            lw  = 95.0
            lx  = float(vx + 6) if right else float(vx - lw - 6)
            g.DrawString(label, fnt_lbl, brush_dim,
                         RectangleF(lx, mid - 7.0, lw, 14.0), sf_l)

        # -- Jamb: rough opening (left side) --
        ax_ro = pnl_x + cp_px + ro_px // 2   # midpoint of RO zone
        v_arrow(ax_ro, rf_y, op_y + op_h, "Rough Opening", right=False)

        # -- Jamb: clearance to panel (left side) --
        ax_cp = pnl_x + cp_px // 2
        v_arrow(ax_cp, rf_y, op_y + op_h, "Clearance to Panel", right=False)

        # -- Header: top (horizontal) --
        hy = pnl_y + cp_px // 2
        h_arrow(rf_x, op_x + op_w // 2, hy, "Header CLR", above=False)

        # -- Sill: bottom (horizontal) --
        sy = pnl_y + pnl_h - cp_px // 2
        h_arrow(rf_x, op_x + op_w // 2, sy, "Sill CLR", above=True)

        # ---- legend ----
        leg_y = pnl_y + pnl_h + 6
        lx = pnl_x
        items = [
            (c_cp_fill, "Clearance to Panel"),
            (c_ro_fill, "Rough Opening CLR"),
        ]
        for idx, (col, txt) in enumerate(items):
            bx = lx + idx * 130
            with SolidBrush(col) as b:
                g.FillRectangle(b, bx, leg_y, 10, 10)
            with Pen(Color.FromArgb(140, 140, 140), 0.75) as p:
                g.DrawRectangle(p, bx, leg_y, 10, 10)
            with SolidBrush(c_dim) as b:
                g.DrawString(txt, fnt_lbl, b,
                             RectangleF(float(bx + 13), float(leg_y - 1), 115.0, 12.0), sf_l)

        # cleanup
        pen_panel.Dispose()
        pen_dim.Dispose()
        pen_thin.Dispose()
        fnt_lbl.Dispose()
        fnt_bold.Dispose()
        fnt_ttl.Dispose()
        sf_c.Dispose()
        sf_l.Dispose()
        brush_dim.Dispose()
        brush_open.Dispose()
        brush_white.Dispose()

    def _reposition_buttons(self, s, e):
        w = self._bb.ClientSize.Width
        ok_w  = self._btnOK.Width
        can_w = self._btnCancel.Width
        gap   = _scale(10, _DPI_SCALE)
        self._btnOK.Location     = Point(w - ok_w - can_w - gap * 2, _scale(9, _DPI_SCALE))
        self._btnCancel.Location = Point(w - can_w - gap,             _scale(9, _DPI_SCALE))

    def _populate_rows(self):
        y = 4

        # Identity
        y = self._section(y, "Identity")
        y = self._text_row(y, "Project Name", self._cfg.project_name, "_prj")

        # Orientation
        y = self._section(y, "Orientation")
        cur = self._cfg.optimization_strategy.panel_orientation.capitalize()
        y = self._radio_row(y, "Panel Orientation",
                            ["Vertical", "Horizontal"], cur, "_rb_orient")

        # Panel Type
        y = self._section(y, "Panel Type")
        sp = self._cfg.panel_constraints.panel_spacing
        cur_type = "Fully Finished (3/4\")" if abs(sp - 0.75) < 0.01 else "Backer (1/8\")"
        y = self._radio_row(y, "Panel Type / Spacing",
                            ["Backer (1/8\")", "Fully Finished (3/4\")"],
                            cur_type, "_rb_type")

        # Panel Dimensions
        y = self._section(y, "Panel Dimensions")
        pc = self._cfg.panel_constraints
        for lbl, val, attr in [
            ("Min Width (in)",           pc.min_width,           "_dim_min_w"),
            ("Max Width (in)",           pc.max_width,           "_dim_max_w"),
            ("Min Height (in)",          pc.min_height,          "_dim_min_h"),
            ("Max Height (in)",          pc.max_height,          "_dim_max_h"),
            ("Short Max (in)",           pc.short_max,           "_dim_short"),
            ("Long Max (in)",            pc.long_max,            "_dim_long"),
            ("Dimension Increment (in)", pc.dimension_increment, "_dim_inc"),
        ]:
            y = self._num_row(y, lbl, val, attr)

        # Door Clearances
        y = self._section(y, "Door Clearances")
        y = self._clr_header(y)
        dc = self._cfg.door_clearances
        for side, rv, pv, ra, pa in [
            ("Jamb",   dc.rough_jamb,   dc.panel_jamb,   "_dc_rj", "_dc_pj"),
            ("Header", dc.rough_header, dc.panel_header, "_dc_rh", "_dc_ph"),
            ("Sill",   dc.rough_sill,   dc.panel_sill,   "_dc_rs", "_dc_ps"),
        ]:
            y = self._clr_row(y, side, rv, pv, ra, pa)

        # Window Clearances
        y = self._section(y, "Window Clearances")
        y = self._clr_header(y)
        wc = self._cfg.window_clearances
        for side, rv, pv, ra, pa in [
            ("Jamb",   wc.rough_jamb,   wc.panel_jamb,   "_wc_rj", "_wc_pj"),
            ("Header", wc.rough_header, wc.panel_header, "_wc_rh", "_wc_ph"),
            ("Sill",   wc.rough_sill,   wc.panel_sill,   "_wc_rs", "_wc_ps"),
        ]:
            y = self._clr_row(y, side, rv, pv, ra, pa)

        # Storefront Clearances
        y = self._section(y, "Storefront Clearances")
        y = self._clr_header(y)
        sc = self._cfg.storefront_clearances
        for side, rv, pv, ra, pa in [
            ("Jamb",   sc.rough_jamb,   sc.panel_jamb,   "_sc_rj", "_sc_pj"),
            ("Header", sc.rough_header, sc.panel_header, "_sc_rh", "_sc_ph"),
            ("Sill",   sc.rough_sill,   sc.panel_sill,   "_sc_rs", "_sc_ps"),
        ]:
            y = self._clr_row(y, side, rv, pv, ra, pa)

        # Wall Opening Clearances
        y = self._section(y, "Wall Opening Clearances (pure void - zero by default)")
        y = self._clr_header(y)
        woc = self._cfg.wall_opening_clearances
        for side, rv, pv, ra, pa in [
            ("Jamb",   woc.rough_jamb,   woc.panel_jamb,   "_woc_rj", "_woc_pj"),
            ("Header", woc.rough_header, woc.panel_header, "_woc_rh", "_woc_ph"),
            ("Sill",   woc.rough_sill,   woc.panel_sill,   "_woc_rs", "_woc_ps"),
        ]:
            y = self._clr_row(y, side, rv, pv, ra, pa)

        # Optimization Strategy
        y = self._section(y, "Optimization Strategy")
        os_ = self._cfg.optimization_strategy

        # ── Main strategy ────────────────────────────────────────────────
        cur_goal = "Minimize Unique Panels" if os_.minimize_unique_panels else "Largest Panel Possible"
        y = self._radio_row(y, "Optimization Goal",
                            ["Largest Panel Possible", "Minimize Unique Panels"],
                            cur_goal, "_rb_goal")

        DESC_H = self._ROW_H * 2 + _scale(4, _DPI_SCALE)
        for _desc, _bg in [
            ("Largest Panel Possible: Places the biggest panel that meets all constraints "
             "across the facade. Each opening may produce a unique panel type.", CLR_ROW_ALT),
            ("Minimize Unique Panels: Designs identical panels around repeating window "
             "patterns so as many panels as possible share the same fabrication type.", CLR_ROW_ALT),
        ]:
            _dr = self._make_row(y, _bg, DESC_H)
            _dl = Label()
            _dl.Text      = _desc
            _dl.Font      = FNT_SMALL
            _dl.ForeColor = Color.FromArgb(80, 80, 80)
            _dl.AutoSize  = False
            row_w = self._scroll.ClientSize.Width
            if row_w < 100:
                row_w = self.ClientSize.Width - self._DIAG_W - 20
            _dl.Size     = Size(max(row_w - _scale(30, _DPI_SCALE), _scale(400, _DPI_SCALE)),
                                DESC_H - _scale(4, _DPI_SCALE))
            _dl.Location = Point(self._LEFT + _scale(16, _DPI_SCALE), _scale(4, _DPI_SCALE))
            _dr.Controls.Add(_dl)
            self._scroll.Controls.Add(_dr)
            y += DESC_H + 1

        # ── Panels WITH openings (visible only under Minimize Unique) ────
        y = self._section(y, "  Panels with Openings  (applies when Minimize Unique Panels is selected)")
        _cur_align = getattr(os_, "opening_alignment", "opening_derived")
        _align_rev = {
            "opening_derived": "Opening-Derived Width",
            "center":          "Center Openings",
            "set_x_offset":    "Set X Offset",
        }
        _cur_align_lbl = _align_rev.get(_cur_align, "Opening-Derived Width")
        y = self._radio_row(y, "Panel-with-Opening Strategy",
                            ["Opening-Derived Width", "Center Openings", "Set X Offset"],
                            _cur_align_lbl, "_rb_align")

        ALIGN_DESC_H = self._ROW_H * 2 + _scale(4, _DPI_SCALE)
        for _adesc in [
            ("Opening-Derived Width: W = window_spacing - panel_spacing + window_width. "
             "Produces the largest identical panel possible from the window geometry. "
             "Void 1 X Offset Left and Right are derived automatically."),
            ("Center Openings: Finds the most-repeated panel width, then centres the "
             "opening: Void 1 X Offset Left = Void 1 X Offset Right = (W - window_width) / 2."),
            ("Set X Offset: Finds the most-repeated panel width, then uses your specified "
             "Void 1 X Offset Left. Right offset = W - window_width - left_offset."),
        ]:
            _dr = self._make_row(y, CLR_ROW_ALT, ALIGN_DESC_H)
            _dl = Label()
            _dl.Text      = _adesc
            _dl.Font      = FNT_SMALL
            _dl.ForeColor = Color.FromArgb(80, 80, 80)
            _dl.AutoSize  = False
            row_w2 = self._scroll.ClientSize.Width
            if row_w2 < 100:
                row_w2 = self.ClientSize.Width - self._DIAG_W - 20
            _dl.Size     = Size(max(row_w2 - _scale(30, _DPI_SCALE), _scale(400, _DPI_SCALE)),
                                ALIGN_DESC_H - _scale(4, _DPI_SCALE))
            _dl.Location = Point(self._LEFT + _scale(16, _DPI_SCALE), _scale(4, _DPI_SCALE))
            _dr.Controls.Add(_dl)
            self._scroll.Controls.Add(_dr)
            y += ALIGN_DESC_H + 1

        _void1_left_val = getattr(os_, "void1_x_offset_left", 6.0)
        y = self._num_row(y, "Void 1 X Offset Left (in)  [used by Set X Offset]",
                          _void1_left_val, "_void1_x_offset_left")

        # ── Panels WITHOUT openings ──────────────────────────────────────
        y = self._section(y, "  No-Opening Panels  (applies when Minimize Unique Panels is selected)")
        _cur_nw = getattr(os_, "nonwindow_strategy", "largest")
        _nw_rev = {"largest": "Largest Possible", "standardise": "Identical (match standard W)"}
        _cur_nw_lbl = _nw_rev.get(_cur_nw, "Largest Possible")
        y = self._radio_row(y, "No-Opening Panel Strategy",
                            ["Largest Possible", "Identical (match standard W)"],
                            _cur_nw_lbl, "_rb_nw")

        NW_DESC_H = self._ROW_H * 2 + _scale(4, _DPI_SCALE)
        for _nwdesc in [
            ("Largest Possible: Edge and no-opening zones use the largest panel that fits. "
             "These panels may be different widths from each other and from the window panels."),
            ("Identical (match standard W): Use the same width W as the window panels in "
             "edge/no-opening zones too, so the whole facade shares as few unique types as possible."),
        ]:
            _nwdr = self._make_row(y, CLR_ROW_ALT, NW_DESC_H)
            _nwdl = Label()
            _nwdl.Text      = _nwdesc
            _nwdl.Font      = FNT_SMALL
            _nwdl.ForeColor = Color.FromArgb(80, 80, 80)
            _nwdl.AutoSize  = False
            row_w3 = self._scroll.ClientSize.Width
            if row_w3 < 100:
                row_w3 = self.ClientSize.Width - self._DIAG_W - 20
            _nwdl.Size     = Size(max(row_w3 - _scale(30, _DPI_SCALE), _scale(400, _DPI_SCALE)),
                                  NW_DESC_H - _scale(4, _DPI_SCALE))
            _nwdl.Location = Point(self._LEFT + _scale(16, _DPI_SCALE), _scale(4, _DPI_SCALE))
            _nwdr.Controls.Add(_nwdl)
            self._scroll.Controls.Add(_nwdr)
            y += NW_DESC_H + 1

        self._scroll.AutoScrollMinSize = Size(_scale(600, _DPI_SCALE), y + _scale(60, _DPI_SCALE))

    # ---------------------------------------------------------------- row builders

    def _make_row(self, y, bg, h=None):
        row = Panel()
        row.Location  = Point(0, y)
        w = self._scroll.ClientSize.Width
        if w < 100: w = self.ClientSize.Width - self._DIAG_W - 20
        h = h if h is not None else self._ROW_H
        row.Size = Size(max(w, _scale(580, _DPI_SCALE)), h)
        row.Anchor    = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        row.BackColor = bg
        return row

    def _section(self, y, text):
        row = self._make_row(y, CLR_GROUP_BG)
        lbl = Label()
        lbl.Text      = text
        lbl.Font      = FNT_BOLD
        lbl.ForeColor = CLR_GROUP_FG
        lbl.AutoSize  = False
        lbl.Anchor    = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        lbl.Location  = Point(_scale(6, _DPI_SCALE), _scale(5, _DPI_SCALE))
        lbl.Size      = Size(_scale(660, _DPI_SCALE), self._ROW_H)
        row.Controls.Add(lbl)
        self._scroll.Controls.Add(row)
        return y + self._ROW_H + 1

    def _clr_header(self, y):
        row = self._make_row(y, CLR_SUB_BG)
        for txt, x, w in [
            ("Side",                 _scale(6, _DPI_SCALE),  self._LBL_W - _scale(14, _DPI_SCALE)),
            ("Rough Opening (in)",   self._CLR1_X,           _scale(140, _DPI_SCALE)),
            ("Panel Clearance (in)", self._CLR2_X,           _scale(140, _DPI_SCALE)),
            ("Total (in)",           self._TOT_X,            self._TOT_W),
        ]:
            l = Label()
            l.Text      = txt
            l.Font      = FNT_BOLD
            l.ForeColor = Color.FromArgb(55, 55, 55)
            l.AutoSize  = False
            l.Size      = Size(w, self._ROW_H)
            l.Location  = Point(x, _scale(5, _DPI_SCALE))
            row.Controls.Add(l)
        self._scroll.Controls.Add(row)
        return y + self._ROW_H + 1

    def _clr_row(self, y, side, rough_val, panel_val, rough_attr, panel_attr):
        row = self._make_row(y, CLR_ROW_BG)

        lbl = Label()
        lbl.Text     = side
        lbl.Font     = FNT_NORMAL
        lbl.AutoSize = False
        lbl.Size     = Size(self._LBL_W - _scale(6, _DPI_SCALE), self._ROW_H)
        lbl.Location = Point(self._LEFT + _scale(4, _DPI_SCALE), _scale(5, _DPI_SCALE))
        row.Controls.Add(lbl)

        txt_r = TextBox()
        txt_r.Text     = str(rough_val)
        txt_r.Size     = Size(self._TXT_W, _scale(20, _DPI_SCALE))
        txt_r.Location = Point(self._CLR1_X, _scale(3, _DPI_SCALE))
        setattr(self, rough_attr, txt_r)
        row.Controls.Add(txt_r)

        txt_p = TextBox()
        txt_p.Text     = str(panel_val)
        txt_p.Size     = Size(self._TXT_W, _scale(20, _DPI_SCALE))
        txt_p.Location = Point(self._CLR2_X, _scale(3, _DPI_SCALE))
        setattr(self, panel_attr, txt_p)
        row.Controls.Add(txt_p)

        tot = Label()
        tot.Text      = "{:.4g}\"".format(rough_val + panel_val)
        tot.Font      = FNT_NORMAL
        tot.ForeColor = Color.FromArgb(60, 60, 60)
        tot.AutoSize  = False
        tot.Size      = Size(self._TOT_W, self._ROW_H)
        tot.Location  = Point(self._TOT_X, _scale(5, _DPI_SCALE))
        tot.TextAlign = ContentAlignment.MiddleCenter
        row.Controls.Add(tot)

        def make_upd(tr, tp, tl):
            def upd(s, e):
                tl.Text = "{:.4g}\"".format(
                    _safe_float(tr.Text, 0.0) + _safe_float(tp.Text, 0.0))
            return upd
        upd = make_upd(txt_r, txt_p, tot)
        txt_r.TextChanged += upd
        txt_p.TextChanged += upd

        self._scroll.Controls.Add(row)
        return y + self._ROW_H + 1

    def _num_row(self, y, label_text, value, attr):
        row = self._make_row(y, CLR_ROW_BG)

        lbl = Label()
        lbl.Text     = label_text
        lbl.Font     = FNT_NORMAL
        lbl.AutoSize = False
        lbl.Size     = Size(self._LBL_W, self._ROW_H)
        lbl.Location = Point(self._LEFT + _scale(4, _DPI_SCALE), _scale(5, _DPI_SCALE))
        row.Controls.Add(lbl)

        txt = TextBox()
        txt.Text     = str(value)
        txt.Size     = Size(self._TXT_W, _scale(20, _DPI_SCALE))
        txt.Location = Point(self._TXT_X, _scale(3, _DPI_SCALE))
        setattr(self, attr, txt)
        row.Controls.Add(txt)

        hint = Label()
        hint.Text      = "in"
        hint.Font      = FNT_SMALL
        hint.ForeColor = Color.Gray
        hint.AutoSize  = True
        hint.Location  = Point(self._TXT_X + self._TXT_W + _scale(6, _DPI_SCALE), _scale(7, _DPI_SCALE))
        row.Controls.Add(hint)

        self._scroll.Controls.Add(row)
        return y + self._ROW_H + 1

    def _text_row(self, y, label_text, value, attr):
        row = self._make_row(y, CLR_ROW_BG)

        lbl = Label()
        lbl.Text     = label_text
        lbl.Font     = FNT_NORMAL
        lbl.AutoSize = False
        lbl.Size     = Size(self._LBL_W, self._ROW_H)
        lbl.Location = Point(self._LEFT + _scale(4, _DPI_SCALE), _scale(5, _DPI_SCALE))
        row.Controls.Add(lbl)

        txt = TextBox()
        txt.Text     = str(value)
        txt.Size     = Size(_scale(260, _DPI_SCALE), _scale(20, _DPI_SCALE))
        txt.Location = Point(self._TXT_X, _scale(3, _DPI_SCALE))
        setattr(self, attr, txt)
        row.Controls.Add(txt)

        self._scroll.Controls.Add(row)
        return y + self._ROW_H + 1

    def _radio_row(self, y, label_text, options, selected, attr):
        row = self._make_row(y, CLR_ROW_BG)

        lbl = Label()
        lbl.Text     = label_text
        lbl.Font     = FNT_NORMAL
        lbl.AutoSize = False
        lbl.Size     = Size(self._LBL_W, self._ROW_H)
        lbl.Location = Point(self._LEFT + _scale(4, _DPI_SCALE), _scale(5, _DPI_SCALE))
        row.Controls.Add(lbl)

        radios = []
        rx = self._TXT_X
        radio_step = _scale(185, _DPI_SCALE)
        for opt_text in options:
            rb = RadioButton()
            rb.Text     = opt_text
            rb.Checked  = (opt_text == selected)
            rb.AutoSize = True
            rb.Font     = FNT_NORMAL
            rb.Location = Point(rx, _scale(4, _DPI_SCALE))
            row.Controls.Add(rb)
            radios.append(rb)
            rx += radio_step

        setattr(self, attr, radios)
        self._scroll.Controls.Add(row)
        return y + self._ROW_H + 1

    # ---------------------------------------------------------------- helpers

    def _selected(self, attr):
        for rb in getattr(self, attr):
            if rb.Checked:
                return rb.Text
        return getattr(self, attr)[0].Text

    def _flt(self, attr, default):
        obj = getattr(self, attr, None)
        return _safe_float(obj.Text, default) if obj else default

    # ---------------------------------------------------------------- OK handler

    def _on_ok(self, sender, e):
        errors = []

        project_name = self._prj.Text.strip()
        if not project_name:
            errors.append("Project Name cannot be empty.")

        orientation   = self._selected("_rb_orient").lower()
        panel_spacing = 0.75 if "Fully" in self._selected("_rb_type") else 0.125

        pc    = self._cfg.panel_constraints
        min_w = self._flt("_dim_min_w", pc.min_width)
        max_w = self._flt("_dim_max_w", pc.max_width)
        min_h = self._flt("_dim_min_h", pc.min_height)
        max_h = self._flt("_dim_max_h", pc.max_height)
        short = self._flt("_dim_short", pc.short_max)
        long_ = self._flt("_dim_long",  pc.long_max)
        inc   = self._flt("_dim_inc",   pc.dimension_increment)

        if min_w <= 0:     errors.append("Min Width must be > 0.")
        if max_w <= min_w: errors.append("Max Width must be > Min Width.")
        if min_h <= 0:     errors.append("Min Height must be > 0.")
        if max_h <= min_h: errors.append("Max Height must be > Min Height.")
        if short <= 0:     errors.append("Short Max must be > 0.")
        if long_ < short:  errors.append("Long Max must be >= Short Max.")
        if inc   <= 0:     errors.append("Dimension Increment must be > 0.")

        for attr in ["_dc_rj","_dc_pj","_dc_rh","_dc_ph","_dc_rs","_dc_ps",
                     "_wc_rj","_wc_pj","_wc_rh","_wc_ph","_wc_rs","_wc_ps",
                     "_sc_rj","_sc_pj","_sc_rh","_sc_ph","_sc_rs","_sc_ps",
                     "_woc_rj","_woc_pj","_woc_rh","_woc_ph","_woc_rs","_woc_ps"]:
            if self._flt(attr, -1) < 0:
                errors.append("Clearance values cannot be negative.")
                break

        if errors:
            MessageBox.Show("\n".join(errors), "Validation Errors",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return

        # Commit
        self._cfg.project_name = project_name
        self._cfg.optimization_strategy.panel_orientation = orientation
        self._cfg.optimization_strategy.prefer_full_height_panels = (orientation == "vertical")

        pc.min_width           = min_w
        pc.max_width           = max_w
        pc.min_height          = min_h
        pc.max_height          = max_h
        pc.short_max           = short
        pc.long_max            = long_
        pc.dimension_increment = inc
        pc.panel_spacing       = panel_spacing

        dc = self._cfg.door_clearances
        dc.rough_jamb   = self._flt("_dc_rj", dc.rough_jamb)
        dc.panel_jamb   = self._flt("_dc_pj", dc.panel_jamb)
        dc.rough_header = self._flt("_dc_rh", dc.rough_header)
        dc.panel_header = self._flt("_dc_ph", dc.panel_header)
        dc.rough_sill   = self._flt("_dc_rs", dc.rough_sill)
        dc.panel_sill   = self._flt("_dc_ps", dc.panel_sill)

        wc = self._cfg.window_clearances
        wc.rough_jamb   = self._flt("_wc_rj", wc.rough_jamb)
        wc.panel_jamb   = self._flt("_wc_pj", wc.panel_jamb)
        wc.rough_header = self._flt("_wc_rh", wc.rough_header)
        wc.panel_header = self._flt("_wc_ph", wc.panel_header)
        wc.rough_sill   = self._flt("_wc_rs", wc.rough_sill)
        wc.panel_sill   = self._flt("_wc_ps", wc.panel_sill)

        sc = self._cfg.storefront_clearances
        sc.rough_jamb   = self._flt("_sc_rj", sc.rough_jamb)
        sc.panel_jamb   = self._flt("_sc_pj", sc.panel_jamb)
        sc.rough_header = self._flt("_sc_rh", sc.rough_header)
        sc.panel_header = self._flt("_sc_ph", sc.panel_header)
        sc.rough_sill   = self._flt("_sc_rs", sc.rough_sill)
        sc.panel_sill   = self._flt("_sc_ps", sc.panel_sill)

        woc = self._cfg.wall_opening_clearances
        woc.rough_jamb   = self._flt("_woc_rj", woc.rough_jamb)
        woc.panel_jamb   = self._flt("_woc_pj", woc.panel_jamb)
        woc.rough_header = self._flt("_woc_rh", woc.rough_header)
        woc.panel_header = self._flt("_woc_ph", woc.panel_header)
        woc.rough_sill   = self._flt("_woc_rs", woc.rough_sill)
        woc.panel_sill   = self._flt("_woc_ps", woc.panel_sill)

        # Optimization Strategy
        os_ = self._cfg.optimization_strategy
        os_.minimize_unique_panels = ("Minimize" in self._selected("_rb_goal"))
        tol = self._flt("_strat_tol", os_.cutout_tolerance)
        os_.cutout_tolerance = max(0.0, tol)

        # Opening alignment strategy
        _align_map = {
            "Opening-Derived Width": "opening_derived",
            "Center Openings":       "center",
            "Set X Offset":          "set_x_offset",
        }
        os_.opening_alignment   = _align_map.get(self._selected("_rb_align"),
                                                  "opening_derived")
        os_.void1_x_offset_left = self._flt("_void1_x_offset_left", 6.0)

        # No-opening panel strategy
        _nw_map = {
            "Largest Possible":           "largest",
            "Identical (match standard W)": "standardise",
        }
        os_.nonwindow_strategy = _nw_map.get(self._selected("_rb_nw"), "largest")

        self.DialogResult = DialogResult.OK
        self.Close()


def show_config_dialog(config):
    owner = get_revit_owner()
    dlg = ConfigDialog(config)
    result = dlg.ShowDialog(owner) if owner else dlg.ShowDialog()
    return result == DialogResult.OK


# =================
# Utility functions
# =================
def _ensure_dir(path):
    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except Exception as e:
            print("Failed to create directory {0}: {1}".format(path, e))

def _sanitize_folder_name(name):
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name


# =====
# Main
# =====
def main():
    try:
        Application.EnableVisualStyles()
    except Exception as e:
        print("EnableVisualStyles failed: {0}".format(e))

    # 1) Pick input folder
    input_dir = pick_data_folder()
    _ensure_dir(input_dir)

    # 2) Load starting config (saved file if present, else vertical preset)
    config_file = os.path.join(input_dir, "optimizer_config.json")
    presets = opt.get_preset_configs()
    if os.path.exists(config_file):
        try:
            config = opt.OptimizerConfig.load(config_file)
            print("[CONFIG] Loaded from: {}".format(config_file))
        except Exception as ex:
            print("[CONFIG] Failed to load ({}), using preset.".format(ex))
            config = presets["vertical"]
    else:
        config = presets["vertical"]

    # 3) Show the single unified dialog
    if not show_config_dialog(config):
        MessageBox.Show("Operation canceled.", "Panel Optimizer",
                        MessageBoxButtons.OK, MessageBoxIcon.Information)
        return

    orientation     = config.optimization_strategy.panel_orientation
    panel_spacing   = config.panel_constraints.panel_spacing
    panel_type_name = "Fully Finished" if abs(panel_spacing - 0.75) < 0.01 else "Backer"

    # 4) Create timestamped output directory
    timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir   = os.path.join(
        input_dir, "{}_{}".format(_sanitize_folder_name(config.project_name), timestamp))
    _ensure_dir(output_dir)

    # 5) Resolve CSV paths
    walls_csv    = os.path.join(input_dir, "walls.csv")
    openings_csv = os.path.join(input_dir, "wall_openings.csv")

    if not os.path.exists(walls_csv):
        MessageBox.Show(
            "Could not find walls.csv in:\n{0}".format(input_dir),
            "Missing Input", MessageBoxButtons.OK, MessageBoxIcon.Error)
        return

    if not os.path.exists(openings_csv):
        MessageBox.Show(
            "Could not find wall_openings.csv in:\n{0}\n\nProceeding without openings.".format(input_dir),
            "Missing Input", MessageBoxButtons.OK, MessageBoxIcon.Warning)

    # 6) Run optimizer
    walls_rows    = opt.load_walls_from_csv(walls_csv)
    openings_rows = opt.load_openings_from_csv(openings_csv)

    panels_path, config_path = opt.process_all_walls(
        walls_rows, openings_rows, output_dir,
        config.door_clearances,
        config.window_clearances,
        config.storefront_clearances,
        config,
        orientation
    )

    if config_path:
        print("Configuration saved to: {}".format(config_path))

    # 7) Done message
    if panels_path and os.path.exists(panels_path):
        MessageBox.Show(
            "Optimization complete.\n\nPanel Type: {0}\nSpacing: {1}\"\n\nExported panels to:\n{2}".format(
                panel_type_name, panel_spacing, output_dir),
            "Panel Optimizer", MessageBoxButtons.OK, MessageBoxIcon.Information)
    else:
        MessageBox.Show(
            "No panels generated.\nPlease check inputs and configuration.",
            "Panel Optimizer", MessageBoxButtons.OK, MessageBoxIcon.Warning)


# Entrypoint for pyRevit button
if __name__ == "__main__":
    main()