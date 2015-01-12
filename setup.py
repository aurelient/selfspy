# -*- coding: utf-8 -*-
import os
import platform
import sys

from setuptools import setup

OPTIONS = {#'argv_emulation': True,
          'includes' : ['sqlalchemy.dialects.sqlite'],
          'iconfile':'assets/eye.icns',
          }

DATA_FILES = ['./assets/eye.png',
              './assets/eye_grey.png',
              './assets/cursor.png',
              './assets/record.png',
              './assets/stop.png',
              './selfspy/Preferences.xib',
              './selfspy/Bookmark.xib',
              './selfspy/Reviewer.xib']

setup(
    name="selfspy",
    app=['selfspy/__init__.py'],
    version='0.2.0',
    setup_requires=["py2app"],
    options={'py2app': OPTIONS},
    data_files=DATA_FILES,
    description= 'Log your computer activity!',
    install_requires=["SQLAlchemy",
        "lockfile",
        "pyobjc-core",
        "pyobjc-framework-Cocoa",
        "pyobjc-framework-Quartz"
    ]
)
