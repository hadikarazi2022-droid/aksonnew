# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('web', 'web'), ('pdfjs', 'web_pdfjs'), ('icons', 'icons'), ('base.py', '.'), ('api_key.txt', 'akson/')]
binaries = []
hiddenimports = ['aiohttp', 'jinja2', 'pkg_resources.py2_warn', 'pynput', 'pynput.keyboard', 'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtPrintSupport', 'mss', 'cv2', 'pytesseract', 'PIL', 'PIL.Image', 'genanki', 'openai', 'numpy', 'requests', 'webview', 'fitz', 'sqlite3', 'webbrowser', 'subprocess', 'base64', 'queue', 'urllib.parse', 'pathlib']
datas += collect_data_files('akson_cards')
hiddenimports += collect_submodules('akson_cards')
tmp_ret = collect_all('pdfjs')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('web')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['slides_working.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Akson',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icons/akson.png',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Akson',
)
app = BUNDLE(
    coll,
    name='Akson.app',
    icon='icons/akson.png',
    bundle_identifier='ai.akson.viewer',
)
