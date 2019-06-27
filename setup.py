#!/usr/bin/env python

import os

from setuptools import setup, find_packages

from bitcointx import __version__

here = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(here, 'README.md')) as f:
    README = f.read()

requires = []

setup(
    name='python-bitcointx',
    version=__version__,
    description='The Swiss Army Knife of the Bitcoin protocol.',
    long_description=README,
    long_description_content_type='text/markdown',
    classifiers=[
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8-dev",
        "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)",
    ],
    python_requires='>=3.4',
    url='https://github.com/Simplexum/python-bitcointx',
    keywords='bitcoin',
    packages=find_packages(),
    zip_safe=False,
    install_requires=requires,
    test_suite="bitcointx.tests"
)
