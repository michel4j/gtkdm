import argparse
import json
import logging
import os
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
import gtkdm
from gtkdm import widgets, utils, charting
from gtkdm.glade import migrations

PROJECT_DIR = gtkdm.PLUGIN_DIR
logger = utils.create_logger()


def main():
    parser = argparse.ArgumentParser(description='Gtk Display Manager for EPICS.')
    parser.add_argument('display', metavar='display', type=str, help='Display File Name')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose Logging')
    parser.add_argument('-m', '--macros', type=str, help='Macros', required=False)
    parser.add_argument('--debug', action='store_true', help='Debug')
    args = parser.parse_args()

    if args.verbose or args.debug:
        utils.log_to_console(level=logging.DEBUG)
    else:
        utils.log_to_console(level=logging.INFO)

    widgets.Manager.reset(args.macros)
    widgets.Manager.show_display(args.display, main=True)

    Gtk.main()


def editor():
    parser = argparse.ArgumentParser(description='Gtk Display Manager for EPICS.')
    parser.add_argument('display', metavar='display', nargs="?", type=str, help='Display File Name')
    args = parser.parse_args()

    os.environ['GLADE_CATALOG_SEARCH_PATH'] = gtkdm.PLUGIN_DIR
    os.environ['GLADE_MODULE_SEARCH_PATH'] = gtkdm.PLUGIN_DIR

    if args.display:
        subprocess.check_call(['glade', args.display])
    else:
        subprocess.check_call(['glade'])


def migrate_file(filename):
    tree = ET.parse(filename)
    modified = False

    # rename classes
    for obj_type, to_rename in migrations.RENAME['classes'].items():
        selector = f".//object[@class='{obj_type}']"
        for obj in tree.findall(selector):
            obj_class = obj.attrib['class']
            if obj_class in to_rename:
                new_name = to_rename[obj_class]
                obj.attrib['class'] = new_name
                logger.info(f"Renaming:  {obj_class} to {new_name}")
                modified = True

    # remove classes
    for obj_type, to_remove in migrations.REMOVE['classes']:
        selector = f".//object[@class='{obj_type}']"
        for obj in tree.findall(selector):
            obj_class = obj.attrib['class']
            if obj_class in to_remove:
                tree.remove(obj)
                logger.info(f"Removing:  {obj_class}")
                modified = True

    # rename properties
    for obj_type, to_rename in migrations.RENAME['properties'].items():
        selector = f".//object[@class='{obj_type}']"
        for obj in tree.findall(selector):
            for prop in obj.findall('property'):
                prop_name = prop.attrib['name']

                if prop_name in to_rename:
                    new_name = to_rename[prop_name]
                    prop.attrib['name'] = new_name
                    logger.info(f"Renaming:  {obj_type}.{prop_name} to {obj_type}.{new_name}")
                    modified = True

    # remove properties
    for obj_type, to_remove in migrations.REMOVE['properties'].items():
        selector = f".//object[@class='{obj_type}']"
        for obj in tree.findall(selector):
            for prop in obj.findall('property'):
                prop_name = prop.attrib['name']
                if prop_name in to_remove:
                    obj.remove(prop)
                    logger.info(f"Removing:  {obj_type}.{prop_name}")
                    modified = True

    if modified:
        backup_file = f'{filename}.bk'
        os.rename(filename, backup_file)
        tree.write(filename, encoding='utf-8')
        logger.info(f'{filename} overwritten. Previous file backed-up as {backup_file}')
    else:
        logger.info(f"No changes. {filename} not modified!")


def migrate():
    parser = argparse.ArgumentParser(description='Migrate Gtk Display Manager UI file.')
    parser.add_argument('files', metavar='Files', type=str, help='Display Files', nargs='+')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose Logging')
    args = parser.parse_args()

    if args.verbose:
        utils.log_to_console(level=logging.DEBUG)
    else:
        utils.log_to_console(level=logging.INFO)
    for filename in args.files:
        logger.info(f'Migrating display file {filename} ...')
        migrate_file(filename)


def make_symbol():
    parser = argparse.ArgumentParser(description='Create Gtk DM Symbol')
    parser.add_argument('-n', '--name', type=str, help='Symbol Name', required=True)
    parser.add_argument('images', metavar='images', type=str, nargs='+', help='State files in sequence')
    args = parser.parse_args()

    sym_file = '{}.sym'.format(args.name)
    with zipfile.ZipFile(sym_file, 'w', zipfile.ZIP_DEFLATED) as sym:
        names = []
        for image in args.images:
            directory, file_name = os.path.split(image)
            if directory:
                os.chdir(directory)
            if os.path.exists(file_name):
                if file_name not in names:
                    print('Adding {} to {} ...'.format(file_name, sym_file))
                    sym.write(file_name)
                names.append(file_name)
            else:
                print('{} not found! Skipping ...'.format(image))
        sym.writestr('symbol.json', json.dumps(names))
    print('{} ready.'.format(sym_file))


def charting_main():
    parser = argparse.ArgumentParser(description='Gtk Display Manager Charting')
    parser.add_argument('chart', nargs='?', metavar='display', type=str, help='Chart File Name')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose Logging')
    args = parser.parse_args()

    if args.verbose:
        utils.log_to_console(level=logging.DEBUG)
    else:
        utils.log_to_console(level=logging.INFO)

    charting.Manager.load_chart(args.chart)
    Gtk.main()

