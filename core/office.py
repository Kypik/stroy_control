import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class OfficeConversionError(Exception):
    pass


def find_soffice():
    configured = os.environ.get('LIBREOFFICE_PATH')
    if configured and os.path.isfile(configured):
        return configured
    for command in ('soffice', 'soffice.exe'):
        path = shutil.which(command)
        if path:
            return path
    candidates = [
        r'C:\Program Files\LibreOffice\program\soffice.exe',
        r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
        '/usr/bin/soffice',
        '/usr/local/bin/soffice',
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    for path in sorted(glob.glob('/opt/libreoffice*/program/soffice')):
        if os.path.isfile(path):
            return path
    return None


def convert_xlsx_to_pdf(xlsx_path, pdf_path, timeout=120):
    """Преобразовать через LibreOffice и атомарно поместить результат в pdf_path."""
    soffice = find_soffice()
    if not soffice:
        raise OfficeConversionError('LibreOffice (soffice) не найден.')

    output_dir = Path(pdf_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Профиль и вывод — в системном temp: LibreOffice на Windows падает
        # (0xC0000409), если путь к профилю длиннее ~150 символов, а папка
        # предпросмотра в media вложенная и легко переходит этот предел.
        with tempfile.TemporaryDirectory(prefix='office_') as work_dir:
            profile = Path(work_dir, 'profile').as_uri()
            result = subprocess.run(
                [
                    soffice, f'-env:UserInstallation={profile}', '--headless',
                    '--convert-to', 'pdf', '--outdir', work_dir, str(xlsx_path),
                ],
                capture_output=True, timeout=timeout,
            )
            generated = Path(work_dir, Path(xlsx_path).with_suffix('.pdf').name)
            if result.returncode != 0 or not generated.is_file():
                details = result.stderr.decode('utf-8', 'replace')[:500]
                raise OfficeConversionError(details or 'LibreOffice не создал PDF.')
            try:
                os.replace(generated, pdf_path)
            except OSError:
                # temp и media могут быть на разных дисках
                shutil.move(str(generated), str(pdf_path))
    except (OSError, subprocess.SubprocessError) as e:
        raise OfficeConversionError(str(e)) from e
    return str(pdf_path)
