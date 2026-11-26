"""Pack a staged UNO Q offline installer and verify its Debian repository."""
from email.parser import Parser
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def debian_index(stage):
    packages = {}
    indexed = {}
    for stanza in (stage / 'debs/Packages').read_text().strip().split('\n\n'):
        row = Parser().parsestr(stanza)
        name = row['Package']
        filename = (row['Filename'] or '').removeprefix('./')
        if (not name or not row['Version'] or row['Architecture'] not in ('all', 'arm64')
                or name in packages or filename in indexed or '/' in filename
                or not filename.startswith(name + '_') or not filename.endswith('.deb')):
            raise ValueError(f'Invalid or duplicate offline package: {name} {filename}')
        packages[name] = row['Version']
        indexed[filename] = (int(row['Size'] or '0'), row['SHA256'])

    required = Path(__file__).with_name('full-bundle-packages.txt').read_text().splitlines()
    missing = set(required).difference(packages)
    if missing:
        raise ValueError('Missing required offline packages: ' + ', '.join(sorted(missing)))
    debs = {p.name for p in (stage / 'debs').glob('*.deb')}
    if debs != indexed.keys():
        raise ValueError('Offline index and .deb files differ: ' + ', '.join(sorted(debs ^ indexed.keys())))
    return indexed


def package_full(stage, output):
    if output.suffix != '.zip' or output.is_relative_to(stage):
        raise ValueError('Write the full ZIP outside the staging directory.')
    required_files = ('install.sh', 'INSTALL.txt', 'runner.tar', 'image.json', 'upload-tools/sources.json')
    for name in required_files:
        if not (stage / name).is_file():
            raise ValueError(f'Missing full bundle file: {name}')
    if len(list(stage.glob('*.ard'))) != 1:
        raise ValueError('Stage exactly one versioned .ard in the full bundle.')
    indexed = debian_index(stage)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.xgoduck-full-', dir=output.parent) as temp:
        packed = Path(temp) / output.name
        checksums = []
        with ZipFile(packed, 'w', compression=ZIP_DEFLATED) as archive:
            for source in sorted(stage.rglob('*')):
                if source.is_symlink():
                    raise ValueError(f'Unexpected symlink in full bundle: {source}')
                name = source.relative_to(stage).as_posix()
                if not source.is_file() or name == 'SHA256SUMS':
                    continue
                info = ZipInfo.from_file(source, name)
                info.compress_type = ZIP_DEFLATED
                digest = hashlib.sha256()
                with source.open('rb') as src, archive.open(info, 'w') as dest:
                    while chunk := src.read(1024 * 1024):
                        digest.update(chunk)
                        dest.write(chunk)
                checksum = digest.hexdigest()
                if source.parent == stage / 'debs' and source.suffix == '.deb':
                    if indexed[source.name] != (info.file_size, checksum):
                        raise ValueError(f'Offline package size or SHA-256 mismatch: {name}')
                checksums.append(f'{checksum}  {name}\n')
            archive.writestr('SHA256SUMS', ''.join(checksums))

        digest = hashlib.sha256()
        with packed.open('rb') as src:
            while chunk := src.read(1024 * 1024):
                digest.update(chunk)
        checksum = digest.hexdigest()
        sidecar = Path(temp) / (output.name + '.sha256')
        sidecar.write_text(f'{checksum}  {output.name}\n')
        packed.replace(output)
        sidecar.replace(output.with_suffix('.zip.sha256'))
    print(output, checksum)
