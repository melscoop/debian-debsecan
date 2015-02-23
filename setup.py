#!/usr/bin/env python

from setuptools import setup

# Fetch version variable without creating import race condition.
exec(open('debsecan/_version.py').read())

CLASSIFIERS = map(str.strip,
"""Environment :: Console
License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)
Natural Language :: English
Operating System :: POSIX :: Linux
Programming Language :: Python
Programming Language :: Python :: 2.7
Topic :: Security
""".splitlines())

entry_points = {
    'console_scripts': [
        'debsecan = debsecan:main',
    ]
}

setup(
    name="debsecan",
    version=__version__,
    author="Florian Weimer",
    author_email="fw@deneb.enyo.de",
    description="The Debian Security Analyzer",
    license="GPLv2+",
    url="https://gitorious.org/debsecan/debsecan/source/master",
    long_description="",
    classifiers=CLASSIFIERS,
    keywords="desktop security",
    install_requires=[
    ],
    packages=['debsecan'],
    package_dir={'debsecan': 'debsecan'},
    platforms=['Linux'],
    zip_safe=False,
    entry_points=entry_points,
    # Used by setup.py bdist to include files in the binary package
    # package_data={'debsecan': []},
)
