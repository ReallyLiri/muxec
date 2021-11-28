from setuptools import setup, find_packages

__version__ = '0.1.0'

with open("README.md", "r") as f:
    long_description = f.read()

setup(
    name="muxec",
    description="Multiplexed Exec Tool",
    long_description=long_description,
    long_description_content_type="text/markdown",
    version=__version__,
    author='ReallyLiri',
    url='https://github.com/ReallyLiri/muxec',
    author_email='reallyliri@gmail.com',
    keywords='mux multiplex exec execute xargs shell parallel',
    py_modules=['muxec'],
    python_requires='>=3.6',
    packages=find_packages(),
    entry_points='''
        [console_scripts]
        muxec=muxec:main
    ''',
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
)
