import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.append(PLUGIN_DIR)

from gtkdm import widgets


def do_post_create(*args, **kwargs):
    print(widgets, *args, **kwargs)
