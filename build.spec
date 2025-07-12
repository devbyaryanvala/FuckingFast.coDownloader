# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import sys
import os
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.building.build_main import PYZ, EXE, COLLECT

# Application name and version
APP_NAME = 'FuckingFast Downloader' # You might want to change this to 'Fucking Fast Downloader'
APP_VERSION = '1.0'

# Platform-specific configurations
if sys.platform == 'win32':
    ICON_PATH = os.path.join('icons', 'logo.ico')

# List of data files to include
data_files = []

# Collect qt_material files
data_files.extend(collect_data_files('qt_material'))

# Application icons
data_files.append((ICON_PATH, 'icons'))
data_files.append((os.path.join('icons', 'logo.ico'), 'icons'))

# Required files
data_files.append(('input.txt', '.'))

# Windows specific DLLs
if sys.platform == 'win32':
    dll_path = os.path.join(sys.base_prefix, 'DLLs', 'libcrypto-1_1.dll')
    if os.path.exists(dll_path):
        data_files.append((dll_path, '.'))


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=data_files,
    hiddenimports=[
        'PyQt5.sip',
        'bs4',
        'requests'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

# Executable configuration
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME, # Consider changing this to 'Fucking Fast Downloader'
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
    version_info={
        'CompanyName': 'devbyaryanvala',
        'FileDescription': 'Fucking Fast Downloader', # Corrected: added quotes
        'ProductName': 'Fucking Fast Downloader',     # Corrected: added quotes
        'ProductVersion': '1.0.0', # Corrected: changed to string
        'OriginalFilename': 'Fucking Fast Downloader' + '.exe'
    }
)

# Collect build artifacts
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME # Consider changing this to 'Fucking Fast Downloader'
)