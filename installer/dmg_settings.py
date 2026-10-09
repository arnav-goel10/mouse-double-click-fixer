# Layout of the disk image window, read by dmgbuild:
#
#     dmgbuild -s installer/dmg_settings.py -D app="dist/Mouse Double-Click Fixer.app" \
#         "Mouse Double-Click Fixer" dist/DoubleClickFixer.dmg
#
# The icon positions line up with the arrow drawn in the background
# (installer/make_icons.py), so change both together.
import os.path

app = defines.get("app", "dist/Mouse Double-Click Fixer.app")  # noqa: F821 - provided by dmgbuild
app_name = os.path.basename(app)

format = "UDZO"
filesystem = "HFS+"
files = [app]
symlinks = {"Applications": "/Applications"}
hide_extensions = [app_name]

icon = "installer/assets/icon.icns"
background = "installer/assets/dmg-background.tiff"

# A plain window: no toolbar, sidebar, path or status bar, just the picture.
# The height adds the title bar to the 640 x 360 background.
window_rect = ((200, 140), (640, 388))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
show_icon_preview = False

arrange_by = None
icon_size = 128
text_size = 13
label_pos = "bottom"
icon_locations = {
    app_name: (170, 170),
    "Applications": (470, 170),
}
