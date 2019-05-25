import os
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GObject, Gdk, Gio

PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'glade')
css = Gtk.CssProvider()
with open(os.path.join(PLUGIN_DIR, 'style.css'), 'rb') as handle:
    css_data = handle.read()
    css.load_from_data(css_data)
style = Gtk.StyleContext()
style.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)



