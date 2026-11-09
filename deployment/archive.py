"""Export the verified GHCR deployment bundle for an operator download."""
import gzip
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import zipfile

from deployment.registry import fetch_test


def export_test(repository, selector, next_version, output, client=None):
    output = Path(output)
    zipped = output.suffix == ".zip"
    if not zipped and not output.name.endswith(".tar.gz"):
        raise ValueError("Expected a .tar.gz or .zip destination")
    with TemporaryDirectory(dir=output.parent) as scratch:
        root = Path(scratch) / "bundle"
        receipt = fetch_test(repository, selector, next_version, root, client)
        # Only files from this fresh, verified download enter the archive.
        members = sorted(path for path in root.rglob("*") if path.is_file())
        archive = Path(scratch) / "download"
        if zipped:
            with zipfile.ZipFile(archive, "w") as stream:
                for path in members:
                    info = zipfile.ZipInfo(path.relative_to(root).as_posix())
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    stream.writestr(info, path.read_bytes(), zipfile.ZIP_DEFLATED)
        else:
            with archive.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as stream:
                    for path in members:
                        info = tarfile.TarInfo(path.relative_to(root).as_posix())
                        info.size = path.stat().st_size
                        with path.open("rb") as source:
                            stream.addfile(info, source)
        # Publish the complete file exclusively, even with concurrent exporters.
        os.link(archive, output)
    return receipt
