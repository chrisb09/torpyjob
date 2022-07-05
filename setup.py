from io import open
from os import path

from setuptools import setup, find_packages

here = path.abspath(path.dirname(__file__))

with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='torpyjob',
    version='1.0.0',
    zip_safe=False,
    description='Simple Library for scheduling jobs with unique ipv4 addresses',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='',
    author='Christian F. Brinkmann',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
    keywords='python proxy anonymity privacy socks tor protocol onion hiddenservice jobs schedule',
    packages=find_packages(exclude=['tests']),
    python_requires='>=3.6',
    entry_points={'console_scripts': ['torpyjob=torpyjob.api:main']
                  },
    project_urls={
        'Bug Reports': '',
        'Source': '',
    },
)
