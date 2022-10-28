import contextlib
import re
import unicodedata
from collections import namedtuple
from enum import Enum, EnumMeta

from gi.repository import GObject, Gtk, Pango, Gdk

from . import colors

Column = namedtuple('Column', ['title', 'type', 'text', 'expand', 'editable', 'min_width'])
Column.__new__.__defaults__ = (None,) * len(Column._fields)


class ColumnType(Enum):
    TEXT, TOGGLE, ICON, FLOAT, INT, COLOR = range(6)


DATA_TYPES = {
    ColumnType.TEXT: str,
    ColumnType.TOGGLE: bool,
    ColumnType.ICON: str,
    ColumnType.INT: int,
    ColumnType.FLOAT: float,
    ColumnType.COLOR: str
}


class Table(GObject.GObject):
    Columns = {
        'a': Column(title='A', type=ColumnType.TEXT, text='{}', expand=True, editable=False),
        'b': Column(title='B', type=ColumnType.TOGGLE, text='{:0.3f}', expand=False, editable=False),
    }
    Icons = {  # (icon-name, color)
        'a': ('', '#770000'),
        'b': ('', '#770000'),
    }
    tooltips = None
    parent = 'a'  # The column used to group items under the same parent
    flat = False  # whether tree is flat single level or not
    single_click = False
    select_multiple = False
    sortable = True

    def __init__(self, view, model=None, colormap=None):
        super().__init__()
        self.keys = tuple(self.Columns.keys())
        self.types = tuple(DATA_TYPES[c.type] for c in self.Columns.values())

        if not model:
            self.model = Gtk.TreeStore(*self.types)  # make a new model if none is provided
        else:
            self.model = model

        self.view = view
        self.view.props.enable_grid_lines = Gtk.TreeViewGridLines.BOTH
        self.view.set_model(self.model)
        self.column_info = {}
        self.add_columns()
        self.selection = self.view.get_selection()
        if self.select_multiple:
            self.selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        self.selection.connect('changed', self.do_selection_changed)
        self.model.connect('row-changed', self.row_changed)
        self.model.connect('row-deleted', self.row_deleted)
        self.model.connect('row-inserted', self.row_inserted)
        self.view.props.activate_on_single_click = self.single_click
        self.view.connect('row-activated', self.row_activated)
        self.view.connect('row-activated', self.color_activated)

    def size(self):
        return len(self.model)

    def add_item(self, item, add_parent=True):
        """
        Add an item to the tree
        :param item: a dict
        :return: a tuple of Gtk.TreePath objects for (parent, child), parent path is None for flat trees
        """
        if not self.flat:
            parent_path = None
            parent_itr = self.find_parent_iter(item)
            if parent_itr:
                if not self.model.iter_has_child(parent_itr) and add_parent:
                    row = list(self.model[parent_itr])
                    self.model.append(parent_itr, row=row)
                parent_path = self.model.get_path(parent_itr)
        else:
            parent_itr = parent_path = None

        row = [item.get(key) for key in self.keys]
        child_itr = self.model.append(parent_itr, row=row)
        child_path = self.model.get_path(child_itr)

        return parent_path, child_path

    def find_parent_iter(self, item):
        """
        Find the parent row for a given item.
        :param item: a dict of values for the item about to be added
        :return: a Gtk.TreeItr or None pointing to the parent row
        """
        parent_key = self.keys[self.parent.value]
        parent = self.model.get_iter_first()
        while parent:
            if self.model[parent][self.parent.value] == item.get(parent_key):
                break
            parent = self.model.iter_next(parent)
        return parent

    def add_items(self, items):
        """
        Add a list of items to the data store
        :param items: a list of dicts corresponding to the items
        :return: number of groups added
        """
        groups = set()
        for item in items:
            parent_path, child_path = self.add_item(item)
            groups.add(parent_path)
        return len(groups)

    def row_to_dict(self, row):
        """
        Convert a model row into a dictionary
        :param row: TreeModelRow
        :return: dict representing the item
        """
        return dict(zip(self.keys, row))

    def get_item(self, itr):
        """
        Retrieve the item pointed to by itr
        :param itr: Gtk.TreeItr
        :return:  dict representing the item
        """
        return self.row_to_dict(self.model[itr])

    def get_items(self, itr=None):
        """
        Retrieve all items under the given parent, if itr is a child, retrieve all siblings. For flat
        Trees, the list will contain a single item.
        :param itr: Gtk.TreeItr or none to retrieve everything
        :return:  a list of dicts representing the children or siblings
        """
        items = []
        if itr is None and self.flat:
            itr = self.model.get_iter_first()
            while itr:
                item = self.get_item(itr)
                itr = self.model.iter_next(itr)
                items.append(item)
        elif iter is not None and not self.flat:
            if self.model.iter_has_child(itr):
                parent_itr = itr
            else:
                parent_itr = self.model.iter_parent(itr)
            itr = self.model.iter_children(parent_itr)
            while itr:
                item = self.get_item(itr)
                itr = self.model.iter_next(itr)
                items.append(item)
        else:
            item = self.get_item(itr)
            items.append(item)
        return items

    def clear(self):
        """
        Remove all items from the data store
        """
        self.model.clear()

    def clear_selection(self):
        """Remove all selected items"""
        model, selected = self.selection.get_selected_rows()
        for path in selected:
            row = model[path]
            model.remove(row.iter)

    def make_parent(self, row):
        """
        Make a parent item for a given item
        :param row: a dict for an item
        :return: a dict suitable for adding to the model as a parent
        """
        parent_row = [''] * len(self.keys)
        parent_row[0] = row[self.keys.index(self.parent)]
        return parent_row

    def add_columns(self):
        """
        Add Columns to the TreeView and link all signals
        """

        for data, (name, cell) in enumerate(self.Columns.items()):
            if cell.type == ColumnType.TOGGLE:
                renderer = Gtk.CellRendererToggle(activatable=True)
                renderer.connect('toggled', self.row_toggled, data)
                column = Gtk.TreeViewColumn(title=cell.title, cell_renderer=renderer, active=data)
                column.props.sizing = Gtk.TreeViewColumnSizing.FIXED
                column.set_fixed_width(32)
                self.view.append_column(column)
            elif cell.type == ColumnType.COLOR:
                renderer = Gtk.CellRendererText()
                column = Gtk.TreeViewColumn(title=cell.title, cell_renderer=renderer)
                column.props.sizing = Gtk.TreeViewColumnSizing.FIXED
                column.set_fixed_width(32)
                column.set_cell_data_func(renderer, self.format_color, data)
                self.view.append_column(column)
            elif cell.type == ColumnType.ICON:
                renderer = Gtk.CellRendererPixbuf()
                column = Gtk.TreeViewColumn(title=cell.title, cell_renderer=renderer)
                column.props.sizing = Gtk.TreeViewColumnSizing.FIXED
                column.set_fixed_width(32)
                column.set_cell_data_func(renderer, self.format_icon, data)
                self.view.append_column(column)
            else:  # [ColumnType.TEXT, ColumnType.FLOAT, ColumnType.INT]:
                renderer = Gtk.CellRendererText()
                column = Gtk.TreeViewColumn(title=cell.title, cell_renderer=renderer, text=data, editable=cell.editable)
                column.props.sizing = Gtk.TreeViewColumnSizing.FIXED
                renderer.props.ellipsize = Pango.EllipsizeMode.END
                column.set_expand(cell.expand)
                if self.sortable:
                    column.set_sort_column_id(data)

                if cell.editable:
                    renderer.connect('edited', self.cell_edited, data)

                column.set_cell_data_func(renderer, self.format_cell, name)
                if cell.type in [ColumnType.FLOAT, ColumnType.INT]:
                    renderer.set_alignment(0.9, 0.1)
                if cell.min_width:
                    column.set_min_width(cell.min_width)
                self.view.append_column(column)

            self.column_info[column] = {
                'index': data,
                'cell': cell
            }
            if self.tooltips:
                self.view.set_tooltip_column(self.tooltips.value)

    def format_color(self, column, renderer, model, itr, data):
        """
        Format a color color based on a string specification
        :param column: Gtk.TreeViewColumn
        :param renderer: Gtk.CellRenderer
        :param model: Gtk.TreeModel
        :param itr: Gtk.TreeIter
        :param data:    Column
        :return:
        """
        if model.iter_has_child(itr):
            renderer.set_property('text', '')
        else:
            value = model[itr][data]
            color = Gdk.RGBA()
            color.parse(value)
            renderer.set_property("foreground-rgba", color)
            renderer.set_property("text", "█")

    def format_icon(self, column, renderer, model, itr, data):
        """
        Format an icon based on a field value
        :param column: Gtk.TreeViewColumn
        :param renderer: Gtk.CellRenderer
        :param model: Gtk.TreeModel
        :param itr: Gtk.TreeIter
        :param data:    column
        :return:
        """
        if model.iter_has_child(itr):
            renderer.set_property('icon-name', None)
        else:
            value = model[itr][data]
            name, color = self.Icons.get(value, (None, '#ffffff'))
            rgba = Gdk.RGBA()
            rgba.parse(color)
            theme = Gtk.IconTheme.get_default()
            info = theme.lookup_icon(name, 16, Gtk.IconLookupFlags.FORCE_SYMBOLIC)
            icon, is_symbolic = info.load_symbolic(rgba, None, None, None)
            renderer.props.pixbuf = icon

    def format_cell(self, column, renderer, model, itr, name):
        """
        Method to format cell when values change
        :param column: Gtk.TreeViewColumn
        :param renderer: Gtk.CellRenderer
        :param model: Gtk.TreeModel
        :param itr: Gtk.TreeIter
        :param name:  column name
        :return:
        """
        data = self.keys.index(name)
        cell = self.Columns[name]
        if model.iter_has_child(itr):
            parent_row = self.make_parent(model[itr])
            renderer.set_property('text', parent_row[data])
        else:
            renderer.set_property('text', cell.text.format(model[itr][data]))

    def row_toggled(self, cell, path, data):
        """
        Method to handle toggling of cells
        :param cell: Gtk.CellRendererToggle
        :param path: Gtk.TreePath
        :param data: column
        :return:
        """
        model = self.view.get_model()
        model[path][data] = not self.model[path][data]

    def cell_edited(self, cell, path, text, data):
        """
        Method to handle editing of cells
        :param cell: Gtk.CellRendererText
        :param path: Gtk.TreePath
        :param text: new text
        :return:
        """
        model = self.view.get_model()
        try:
            value = self.types[data](text)
        except ValueError:
            pass
        else:
            model[path][data] = value

    def do_selection_changed(self, selection):
        """
        Handle changes to the selection
        :param selection: Gtk.TreeSelection
        :return:
        """
        if selection.get_mode() != Gtk.SelectionMode.MULTIPLE:
            model, itr = selection.get_selected()
            return self.selection_changed(model, itr)

    def selection_changed(self, model, itr):
        """
        Handle changes to the selection
        :param selection: Gtk.TreeModel
        :param itr: Gtk.TreeIter
        :return:
        """
        pass

    def color_activated(self, view, path, column):
        """
        Handle activation of rows
        :param view: Gtk.TreeView
        :param path: Gtk.TreePath
        :param column: Gtk.TreeViewColumn
        :return:
        """
        info = self.column_info.get(column, {})
        if not info:
            return
        else:
            data = info['index']
            cell = info['cell']
            model = view.get_model()
            if cell.type == ColumnType.COLOR and cell.editable:
                dialog = Gtk.ColorChooserDialog()
                if dialog.run() == Gtk.ResponseType.OK:
                    color = dialog.get_rgba()
                    model[path][data] = colors.rgb_to_hex(color.red, color.green, color.blue)
                dialog.destroy()






    def row_activated(self, view, path, column):
        """
        Handle activation of rows
        :param view: Gtk.TreeView
        :param path: Gtk.TreePath
        :param column: Gtk.TreeViewColumn
        :return:
        """

    def row_changed(self, model, path, itr):
        """
        :param model: Gtk.TreeModel
        :param path: Gtk.TreePath
        :param itr: Gtk.TreeIter
        :return:
        """

    def row_inserted(self, model, path, itr):
        """
        :param model: Gtk.TreeModel
        :param path: Gtk.TreePath
        :param itr: Gtk.TreeIter
        :return:
        """
        parent_itr = model.iter_parent(itr)
        if parent_itr:
            parent = model.get_path(parent_itr)
            self.view.expand_row(parent, False)
        child = model.get_path(itr)
        self.view.scroll_to_cell(child, None, True, 0.5, 0.5)

    def row_deleted(self, model, path):
        """
        :param model: Gtk.TreeModel
        :param path: Gtk.TreePath
        :return:
        """


def slugify(value, empty="", allow_unicode=False):
    """
    Convert to ASCII if 'allow_unicode' is False. Convert spaces to hyphens.
    Remove characters that aren't alphanumerics, underscores, or hyphens.
    Convert to lowercase. Also strip leading and trailing whitespace.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip()
    return re.sub(r'[-\s]+', '-', value)


class Validator(object):
    """
    Collection of Field Validation Converters
    """
    class Clip(object):
        """
        Convert a value to the specified type and clip it between the specified limits
        """
        def __init__(self, conv, lo=None, hi=None, fmt='{}', default=None):
            self.conv = conv
            self.lo = lo
            self.hi = hi
            self.fmt = fmt
            self.default = self.lo if default is None else default

        def clean(self, val):
            try:
                if self.lo is None and self.hi is None:
                    return self.conv(val)
                elif self.lo is None and self.hi is not None:
                    return min(self.conv(val), self.hi)
                elif self.hi is None and self.lo is not None:
                    return max(self.lo, self.conv(val))
                else:
                    return min(max(self.lo, self.conv(val)), self.hi)
            except (TypeError, ValueError):
                return self.default

        def format(self, val):
            return self.fmt.format(self.clean(val))

    class Float(Clip):
        """
        Convert a value to the specified type and clip it between the specified limits
        """
        def __init__(self, **kwargs):
            super().__init__(float, **kwargs)

    class Angle(Float):

        def __init__(self, hi=360, lo=0, **kwargs):
            kwargs['hi'] = hi
            kwargs['lo'] = lo
            super().__init__(**kwargs)

        def clean(self, val):
            try:
                fval = self.conv(val)
                return (fval - self.lo) % (self.hi - self.lo)
            except (TypeError, ValueError):
                return self.default

    class Int(Clip):
        """
        Convert a value to the specified type and clip it between the specified limits
        """
        def __init__(self, **kwargs):
            super().__init__(int, **kwargs)

    class String(Clip):
        """
        Enforce maximum string length
        """
        def __init__(self, max_length=None, default='', **kwargs):
            kwargs['default'] = default
            self.max_length = max_length
            super().__init__(str, **kwargs)

        def clean(self, val):
            return str(val)[:self.max_length]

    class Slug(String):
        def clean(self, val):
            return slugify(super().clean(val))

    class Enum(Clip):
        """
        Make sure value is valid Enum
        """
        def __init__(self, conv: EnumMeta, mode='value', **kwargs):
            if isinstance(kwargs.get('default'), int):
                kwargs['default'] = conv(kwargs['default'])
            elif isinstance(kwargs.get('default'), str):
                kwargs['default'] = conv[kwargs['default']]
            elif not kwargs.get('default', conv):
                kwargs['default'] = list(conv)[0]

            super().__init__(conv, **kwargs)
            self.mode = mode

        def clean(self, val):
            if isinstance(val, self.conv):
                return val
            else:
                try:
                    if self.mode == 'value':
                        return self.conv(int(val))
                    else:
                        return self.conv[str(val)]
                except (TypeError, ValueError):
                    return self.default

        def format(self, val):
            clean_value = self.clean(val)
            if self.mode == 'value':
                return clean_value.value
            else:
                return clean_value.name

    class Bool(object):
        """
        Convert a value to the specified type
        """
        def __init__(self, default=False):
            self.default = default

        def clean(self, val):
            try:
                return bool(val)
            except (TypeError, ValueError):
                return self.default

        def format(self, value):
            return '{}'.format(int(self.clean(value)))

    class Value(object):
        """
        Convert a value to the specified type
        """
        def __init__(self, conv, default=None):
            self.conv = conv
            self.default = default

        def clean(self, val):
            try:
                return self.conv(val)
            except (TypeError, ValueError):
                return self.default

        def format(self, value):
            return '{}'.format(self.clean(value))

    class Pass(Clip):
        def __init__(self):
            super().__init__(lambda v: v)

        def clean(self, val):
            return val

        def format(self, value):
            return '{}'.format(value)


class FieldType(Enum):
    ENTRY, VALUE, TOGGLE, TEXT, CHOICES = range(5)


class FormField(GObject.GObject):
    """
    Detailed Specification of a single config field in a GUI.
    """

    FIELD_TYPES = {
        Gtk.Switch: FieldType.TOGGLE,
        Gtk.TextView: FieldType.TEXT,
        Gtk.SpinButton: FieldType.VALUE,
        Gtk.Scale: FieldType.VALUE,
        Gtk.ComboBoxText: FieldType.CHOICES,
        Gtk.ToggleButton: FieldType.TOGGLE,
        Gtk.ComboBox: FieldType.CHOICES,
        Gtk.Entry: FieldType.ENTRY,     # order is important since SpinButton is a subclass of Entry
    }

    __gsignals__ = {
        'changed': (GObject.SIGNAL_RUN_FIRST, None, (object,))
    }

    def __init__(self, name, widget, validator=Validator.Pass()):
        """
        :param name: field name
        :param widget: field widget
        :param validator: validator or converter
        """
        super().__init__()
        self.name = name
        self.widget = widget
        self.validator = validator
        self.type = None
        for cls, kind in self.FIELD_TYPES.items():
            if isinstance(widget, cls):
                self.type = kind
                break

        self.monitor = self.start_monitor()

    def set_validator(self, converter):
        """
        Change the converter of the field

        :param converter:
        """
        self.validator = converter

    def set_value(self, value):
        """
        Validate and Update the value contained in the GUI input widget referenced by the field spec

        :param name: Field Name
        :param value:  New value to update to
        """

        if self.type == FieldType.ENTRY:
            self.widget.set_text(self.validator.format(value))
        elif self.type == FieldType.TOGGLE:
            self.widget.set_active(self.validator.clean(value))
        elif self.type == FieldType.CHOICES:
            new_value = self.validator.clean(value)
            if self.widget.get_model():
                self.widget.set_active(new_value)
            else:
                self.widget.set_active_id(new_value)
        elif self.type == FieldType.VALUE:
            self.widget.set_value(self.validator.clean(value))
        elif self.type == FieldType.TEXT:
            buffer = self.widget.get_buffer()
            buffer.set_text(self.validator.clean(value))

    def get_value(self):
        """
        Get the validated value from the widget
        """
        value = None
        if self.type == FieldType.ENTRY:
            value =  self.widget.get_text()
        elif self.type == FieldType.TOGGLE:
            value = self.widget.get_active()
        elif self.type == FieldType.CHOICES:
            if self.widget.get_model():
                value = self.widget.get_active()
            else:
                value = self.widget.get_active_id()
        elif self.type == FieldType.VALUE:
            value = self.widget.get_value()
        elif self.type == FieldType.TEXT:
            buffer = self.widget.get_buffer()
            value = buffer.text

        return self.validator.clean(value)

    def default(self):
        if self.validator.hasattr('default'):
            return self.validator.default

    def start_monitor(self):
        """
        Connect change events
        """
        if self.type == FieldType.ENTRY:
            return self.widget.connect('activate', self.send_change)
        elif self.type == FieldType.TOGGLE:
            return self.widget.connect('toggled', self.send_change)
        elif self.type == FieldType.CHOICES:
            return self.widget.connect('changed', self.send_change)
        elif self.type == FieldType.VALUE:
            return self.widget.connect('value-changed', self.send_change)
        elif self.type == FieldType.TEXT:
            return self.widget.connect('focus-out-event', self.send_change)

    def monitor_paused(self):
        """
        Return a blocked monitor context manager
        :return: context manager
        """
        return contextlib.nullcontext() if not self.monitor else self.widget.handler_block(self.monitor)

    def send_change(self, widget, *args):
        """
        handle emission of uniform change signals if field value changes
        :return:
        """

        self.emit('changed', self.get_value())


class Form(object):
    """
    A controller which manages a set of fields in a form within a user interface monitoring, validating inputs
    """

    def __init__(self, fields=(), disabled=()):
        """
        :param fields: a list of Field objects
        """

        self.fields = {
            field.name: field
            for field in fields
        }

        self.disabled = disabled
        self.handlers = {}
        for name, field in self.fields.items():
            if name in self.disabled:
                field.widget.set_sensitive(False)
            field.connect('changed', self.monitor_changes, name)

    def set_values(self, signal=False, **kwargs):
        """
        Set the value of a field by name

        :param kwargs: key, value dictionary
        :param signal: Emit changed signal
        """

        for name, value in kwargs.items():
            if not name in self.fields: continue
            if signal:
                self.fields[name].set_value(value)
            else:
                # pause the monitor before setting the value
                with self.fields[name].monitor_paused():
                    self.fields[name].set_value(value)

    def get_values(self):
        """
        Get the dictionary of all name value pairs
        """
        return {
            name: field.get_value()
            for name, field in self.fields.items()
        }

    def get_defaults(self):
        """
        Return default values
        :return: dictionary
        """

        return {
            name: field.default()
            for name, field in self.fields.items()
        }

    def monitor_changes(self, field, name, value):
        """
        Handle the change event and perform all validations as required

        :param field: the field that emitted the event
        :param name: name of field
        :param value: new cleaned field value
        """
        cleaned_data = self.get_values()
        clean_func_name = f'clean_{name}'
        if hasattr(self, clean_func_name):
            clean_func = getattr(self, clean_func_name)
            values = clean_func(value, cleaned_data)
            self.set_values(**values, signal=False)
