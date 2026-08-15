# PyInstaller spec for the Unlatched engine.
#
# Freezes the Python CLI (unlatched.cli:main) into a standalone onedir
# build named "unlatched-engine". This is what end users get inside the
# Windows installer, so they never need a Python install of their own.
# Developers keep running the plain "unlatched" console script from an
# editable install; PyInstaller is not part of that workflow at all.
#
# Build with (run from the app/ directory so the relative paths below
# resolve, and so unlatched/ is importable from the working directory):
#
#     pyinstaller packaging/engine.spec
#
# packaging/build_release.py drives this for a full release build.

from pathlib import Path

repo_root = Path(SPECPATH).parent
entry_script = str(repo_root / "packaging" / "engine_entry.py")

# The source registry (unlatched/sources/__init__.py) imports every ATS
# module inside a function body so a plain `import unlatched` does not
# pull in every collector's imports up front. PyInstaller's static
# analysis follows that import statement fine, but the modules are
# listed here too so a refactor of that function cannot silently drop
# one of them from a frozen build without a build-time hint.
source_modules = [
    "unlatched.sources.ashby",
    "unlatched.sources.bamboohr",
    "unlatched.sources.breezy",
    "unlatched.sources.greenhouse",
    "unlatched.sources.lever",
    "unlatched.sources.nodesk",
    "unlatched.sources.recruitee",
    "unlatched.sources.remoteok",
    "unlatched.sources.schema_org",
    "unlatched.sources.sitemap",
    "unlatched.sources.smartrecruiters",
    "unlatched.sources.workable",
    "unlatched.sources.workday",
]

a = Analysis(
    [entry_script],
    pathex=[str(repo_root)],
    binaries=[],
    datas=[],
    hiddenimports=source_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "pytest", "_pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="unlatched-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="unlatched-engine",
)
