import sys
import os
import gi

gi.require_version('Gladeui', '2.0')
from gi.repository import Gladeui

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.append(PLUGIN_DIR)

from gtkdm import widgets


class XYPlotAdaptor(Gladeui.WidgetAdaptor):
    __gtype_name__ = 'XYPlotAdaptor'

    def __init__(self):
        super().__init__()

    def do_post_create(self, obj, reason):
        print(obj, reason)


print("Loading", Gladeui, widgets)