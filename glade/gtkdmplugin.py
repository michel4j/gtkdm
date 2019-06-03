import sys
import os
import gi

gi.require_version('Gladeui', '2.0')

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.append(PLUGIN_DIR)

from gtkdm import widgets


def do_post_create(*args, **kwargs):
        print(*args, **kwargs)
