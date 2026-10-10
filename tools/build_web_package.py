"""Construit la distribution Engine et la livre à une web app locale.
Usage : python tools/build_web_package.py --web-repo ../Zanalyze
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-repo', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    web = args.web_repo.resolve()
    if not (web / 'manage.py').is_file() or not (web / 'paris').is_dir():
        parser.error('Le dossier cible doit être la web app Django Zanalyze.')
    with TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', str(root), '--no-deps', '--wheel-dir', tmp], check=True)
        wheel, = Path(tmp).glob('zanalyze_engine-*.whl')
        with zipfile.ZipFile(wheel) as archive:
            files = {name: hashlib.sha256(archive.read(name)).hexdigest()
                     for name in sorted(archive.namelist()) if name.startswith('engine/')}
            for name, digest in files.items():
                if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                    raise RuntimeError(f'Source et paquet différents : {name}')
        vendor = web / 'vendor'
        vendor.mkdir(exist_ok=True)
        target = vendor / wheel.name
        shutil.copyfile(wheel, target)
        version = wheel.name.split('-')[1]
        manifest = {'source': 'https://github.com/Stevy64/Zanalyze-Engine',
                    'version': version, 'wheel': wheel.name,
                    'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'files': files}
        (vendor / 'engine-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        requirements = web / 'requirements.txt'
        lines = [line for line in requirements.read_text(encoding='utf-8').splitlines()
                 if not line.startswith('./vendor/zanalyze_engine-')]
        requirements.write_text('\n'.join(lines).rstrip() + '\n./vendor/' + wheel.name + '\n', encoding='utf-8')
        print(f'Engine {version} livré dans {target}. Installer puis exécuter les tests de la web app.')


if __name__ == '__main__':
    main()
