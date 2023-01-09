import os
import sys

from pathlib import Path
plugin_path = Path(__file__)
PLUGIN_DIR = str(plugin_path.parent.parent.parent)
sys.path.append(PLUGIN_DIR)

from gtkdm import widgets


def do_post_create(*args, **kwargs):
    print(widgets, *args, **kwargs)
