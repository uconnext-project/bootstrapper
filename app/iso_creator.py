import pycdlib
import pathlib
import re
import tempfile
import os

from app.log import Log


def create_iso(sub_isos: list[pathlib.Path], output_file: pathlib.Path) -> None:
    iso = pycdlib.PyCdlib()
    iso.new(joliet=3, rock_ridge='1.09', interchange_level=3)

    iso.add_directory('/DUMMY', joliet_path='/DUMMY', rr_name='DUMMY')
    iso.rm_directory('/DUMMY', joliet_path='/DUMMY', rr_name='DUMMY')

    for sub_iso in sub_isos:
        iso.add_file(
            str(sub_iso),
            iso_path=_create_shortened_path_for_iso(sub_iso),
            rr_name=sub_iso.name,
            joliet_path=f"/{sub_iso.name}",
            file_mode=0o100644
        )

    iso.write(str(output_file))
    iso.close()


def get_manifest_bytes_from_iso(iso_path: pathlib.Path) -> bytes:
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))

    tmp_name = None

    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp_fp:
            tmp_name = tmp_fp.name

            iso.get_file_from_iso_fp(
                tmp_fp,
                rr_path='/etc/manifest.lua'
            )

            tmp_fp.flush()

        with open(tmp_name, 'rb') as f:
            return f.read()

    finally:
        iso.close()

        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def patch_file(path: pathlib.Path, old: bytes, new: bytes) -> pathlib.Path:
    if len(old) != len(new):
        raise ValueError(
            f"Patch length mismatch: {len(old)} != {len(new)}"
        )

    new_file = path.with_name(
        path.stem + "_patched" + path.suffix
    )

    with open(path, "rb") as f:
        content = f.read()

    occurrences = content.count(old)

    if occurrences == 0:
        raise RuntimeError("Pattern not found in file")

    Log.success(f"Found {occurrences} manifest occurrence(s)")

    content = content.replace(old, new)

    with open(new_file, "wb") as f:
        f.write(content)

    return new_file


def _create_shortened_path_for_iso(path: pathlib.Path) -> str:
    stem = path.stem.upper()
    suffix = path.suffix.upper().replace('.', '')

    stem = re.sub(r'[^A-Z0-9_]', '_', stem)
    suffix = re.sub(r'[^A-Z0-9_]', '_', suffix)

    short_stem = stem[:8]
    short_suffix = suffix[:3]

    if short_suffix:
        iso_name = f"{short_stem}.{short_suffix};1"
    else:
        iso_name = f"{short_stem}.;1"

    return f"/{iso_name}"