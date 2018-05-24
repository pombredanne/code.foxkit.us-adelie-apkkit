from codecs import open
from distutils.core import setup
from os import path


here = path.abspath(path.dirname(__file__))


# Get the long description from the README file
with open(path.join(here, 'README.rst'), encoding='utf-8') as f:
    long_description = f.read()


setup(
    name='apkkit',
    version='0.6.0',
    description='Manipulate APK packages and repositories from Python',
    long_description=long_description,
    url='https://code.foxkit.us/adelie/apkkit',
    author='A. Wilcox',
    author_email='awilfox@adelielinux.org',
    license='NCSA',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Software Development :: Libraries',
        'Topic :: System :: Archiving :: Packaging',
        'Topic :: System :: Software Distribution',
    ],
    keywords='adelie,alpine,apk,linux,packaging',
    packages=['apkkit', 'apkkit.base', 'apkkit.io'],
    requires=[
        'cryptography',
        'jinja',
        'pyyaml',
        'requests',
    ]
)
