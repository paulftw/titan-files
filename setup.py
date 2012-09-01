"""
Titan Files
-----------

Titan Files is a filesystem abstraction for App Engine apps, providing a
file-like API which uses the App Engine datastore and blobstore underneath.

Links
`````

* `documentation <http://code.google.com/p/titan-files/>`_
"""
from setuptools import setup


setup(
    name='Titan-Files',
    version='1.0',
    url='http://code.google.com/p/titan-files/',
    license='Apache License 2.0',
    author='Mike Fotinakis',
    author_email='fotinakis@google.com',
    description='Filesystem abstraction for App Engine apps',
    packages=['titan'],
    zip_safe=False,
    platforms='any',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python',
        'Topic :: Software Development :: Libraries :: Python Modules'
    ]
)

