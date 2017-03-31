from distutils.core import setup
import py2exe, sys, os

sys.argv.append('py2exe')

class Target:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.version = "1.0.0.0"
        self.company_name = "TydiWin"
        self.copyright = "Copyright (c) 2017 TydiWin."
        self.name = "TydiWin"

target = Target(
    script = "TydiWin.py",
    icon_resources = [(1, "main_icon.ico")],
    description = "Tydies you Windows",
    dest_base = "TydiWin")

setup(
    options = {'py2exe': {'bundle_files': 2, 'compressed': True}},
    windows = [target],
    zipfile = None,
)