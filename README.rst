=========
 APK Kit
=========
:Authors:
  * **A. Wilcox**
:Version:
  0.6
:Status:
  Beta
:Copyright:
  © 2015-2018 Adélie Linux Team.  NCSA open source licence.



Requirements
============

Background
----------
Adélie Linux, our distribution, uses the Alpine Linux APK package manager.
We need a pure Python library for manipulating and verifying APK packages.
We also need a pure Python library for maintaining APK repositories.


Objectives / success criteria
-----------------------------
* Compatibility with upstream APK Tools.
* Stable v1 release by December 2018.
* Minimal to no external dependencies.




Solution vision
===============

Major features
--------------
#. Pull metadata out of an APK file.

#. Sanity check APK files and repositories.

#. Creation of APK files.

#. Read repository INDEX files to gather information on available packages.




Project Scope and Limitations
=============================

Scope of initial release (v1)
-----------------------------
The initial release will focus primarily on the handling of APK files.  Some
limited repository functionality may be present to further the ends of APK file
management.


Scope of next release (v2)
--------------------------
The second release will focus further on repository management.


Scope of future releases
------------------------
Further releases will focus on keeping up to date with the upstream APK format,
and stability and performance fixes.  No further major features are anticipated.


Limitations and exclusions
--------------------------
.. warning:: This is **NOT** meant to be used by end users, or as a replacement
    package manager!  It is designed for manipulation of APK packages, not
    existing APK databases.  It probably has very little use unless you are a
    package or repository maintainer.
