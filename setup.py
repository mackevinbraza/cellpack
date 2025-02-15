#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""The setup script."""

from setuptools import find_packages, setup

with open("README.md") as readme_file:
    readme = readme_file.read()

setup_requirements = [
    "pytest-runner>=5.2",
]

test_requirements = [
    "black>=19.10b0",
    "codecov>=2.1.4",
    "flake8>=3.8.3",
    "flake8-debugger>=3.2.1",
    "pytest>=5.4.3",
    "pytest-cov>=2.9.0",
    "pytest-raises>=0.11",
]

analysis_requirements = [
    "tqdm>=4.64.1",
    "scikit-learn>=1.1.3",
]

dev_requirements = [
    *setup_requirements,
    *test_requirements,
    *analysis_requirements,
    "bump2version>=1.0.1",
    "coverage>=5.1",
    "ipython>=7.15.0",
    "m2r2>=0.2.7",
    "pytest-runner>=5.2",
    "Sphinx>=3.4.3",
    "sphinx_rtd_theme>=0.5.1",
    "tox>=3.15.2",
    "twine>=3.1.1",
    "wheel>=0.34.2",
]

requirements = [
    "fire>=0.4.0",
    "matplotlib>=3.3.4",
    "numpy>=1.19.2",
    "panda3d==1.10.10",
    "pmw==2.0.1",
    "scipy>=1.6.2",
    "simulariumio>=1.6.3",
    "plotly>=5.3.1",
    "pycollada==0.7.2",
    "pyembree>=0.1.8",
    "pymunk>=6.2.0",
    "trimesh>=3.9.34",
]

extra_requirements = {
    "setup": setup_requirements,
    "test": test_requirements,
    "dev": dev_requirements,
    "all": [
        *requirements,
        *dev_requirements,
        *analysis_requirements,
    ],
}

setup(
    author="Megan Riel-Mehan",
    author_email="meganr@alleninstitute.org",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    description="algorthim to pack molecular recipes",
    entry_points={
        "console_scripts": [
            "analyze=cellpack.bin.analyze:main",
            "convert=cellpack.bin.simularium_converter:main",
            "pack=cellpack.bin.pack:main"
        ]
    },
    install_requires=requirements,
    license="MIT license",
    long_description=readme,
    long_description_content_type="text/markdown",
    include_package_data=True,
    keywords="cellpack",
    name="cellpack",
    packages=find_packages(exclude=["tests", "*.tests", "*.tests.*"]),
    python_requires=">=3.7",
    setup_requires=setup_requirements,
    test_suite="cellpack/tests",
    tests_require=test_requirements,
    extras_require=extra_requirements,
    url="https://github.com/mesoscope/cellpack",
    # Do not edit this string manually, always use bumpversion
    # Details in CONTRIBUTING.rst
    version="1.0.3",
    zip_safe=False,
)
