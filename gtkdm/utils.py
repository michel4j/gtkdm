import contextlib
import os
import re
import logging
import colors

def parse_macro_spec(macro_spec):
    """
    Parse a macro specification and return a dictionary of key-value pairs
    :param macro_spec: Macro string in the format "key=value,key=value,..."
    """
    if macro_spec:
        return dict(re.findall("(\w+)=([^,]*)", macro_spec))
    else:
        return {}


def update_properties(tree, macros):
    """
    Replace macro parameters in properties of xml widget element tree
    :param tree: xml widget element tree
    :param macros: Dictionary containing macro information
    """
    for prop in tree.findall(".//object/property"):
        prop.text = prop.text.format(**macros)


def compress_macro(macros):
    """
    Convert a macros dictionary into a macro specification
    :param macros: dictionary
    :return: Macro specification in the format "key=value,key=value,..."
    """

    return ",".join(["{}={}".format(key, value) for key, value in sorted(macros.items())])


@contextlib.contextmanager
def working_dir(newdir):
    """
    Context Manager for Temporarily switch current working directory
    :param newdir:  New Working directory
    """
    curdir = os.getcwd()
    try:
        os.chdir(newdir)
        yield
    finally:
        os.chdir(curdir)


class NullHandler(logging.Handler):
    """
    A do-nothing log handler.
    """

    def emit(self, record):
        pass


class ColoredConsoleHandler(logging.StreamHandler):
    """
    A colored console log handler
    """
    def format(self, record):
        msg = super(ColoredConsoleHandler, self).format(record)
        if record.levelno == logging.WARNING:
            msg = colors.color(msg, fg=202)
        elif record.levelno > logging.WARNING:
            msg = colors.color(msg, fg=196)
        elif record.levelno == logging.DEBUG:
            msg = colors.color(msg, fg=57)
        return msg


def create_logger(name='gtkdm'):
    """
    Create a logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(NullHandler())
    return logger


def log_to_console(level=logging.DEBUG):
    """
    Add a log handler which logs to the console.
    """

    console = ColoredConsoleHandler()
    console.setLevel(level)
    if level == logging.DEBUG:
        formatter = logging.Formatter('%(asctime)s [%(name)s] %(message)s', '%b/%d %H:%M:%S')
    else:
        formatter = logging.Formatter('%(asctime)s %(message)s', '%b/%d %H:%M:%S')
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)


logger = create_logger()