from setuptools import setup, find_packages
from gtkdm.version import get_version

with open("README.md", "r") as fh:
    long_description = fh.read()

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='gtkdm',
    version=get_version(),
    url="https://github.com/michel4j/gtkdm",
    license='MIT',
    author='Michel Fodje',
    author_email='michel4j@gmail.com',
    description='Python GTK Display Manager for EPICS operator interfaces',
    long_description=long_description,
    long_description_content_type="text/x-markdown",
    keywords='control-system scada display manager epics',
    include_package_data=True,
    packages=find_packages(),
    package_data={
        'gtkdm': [
            'glade/catalog.xml',
            'glade/style.css',
        ]
    },
    install_requires=requirements + [
        'importlib-metadata ~= 1.0 ; python_version < "3.8"',
    ],
    scripts=[
        'bin/gtkdm',
        'bin/gtkdm-editor',
        'bin/gtkdm-mksym',
    ],
    classifiers=[
        'Intended Audience :: Developers',
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
