import io
import logging
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent / '_cache'

_SHAPECHANGE_VERSION = '4.0.0'
_SHAPECHANGE_ZIP_URL = (
    f'https://github.com/ShapeChange/ShapeChange/releases/download/'
    f'{_SHAPECHANGE_VERSION}/ShapeChange-{_SHAPECHANGE_VERSION}.zip'
)
_SHAPECHANGE_JAR = _CACHE_DIR / f'ShapeChange-{_SHAPECHANGE_VERSION}.jar'
_SHAPECHANGE_MARKER = _CACHE_DIR / 'downloaded-shapechange'

_JVM_MAJOR_VERSION = '21'
_JVM_MARKER = _CACHE_DIR / 'downloaded-jvm'
_JVM_DIR = _CACHE_DIR / 'jvm'

_SQLITE_MAGIC = b'SQLite format 3\x00'


def _platform_info():
    system = platform.system().lower()
    machine = platform.machine().lower()
    os_map = {'linux': 'linux', 'darwin': 'mac', 'windows': 'windows'}
    arch_map = {'x86_64': 'x64', 'amd64': 'x64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}
    if system not in os_map:
        raise RuntimeError(f'Unsupported OS: {system}')
    if machine not in arch_map:
        raise RuntimeError(f'Unsupported architecture: {machine}')
    return os_map[system], arch_map[machine]


class _UARedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req.add_unredirected_header('User-Agent', 'bbplugin-shapechange')
        return new_req

_opener = urllib.request.build_opener(_UARedirectHandler)


def _download(url, dest):
    logger.info('Downloading %s -> %s', url, dest)
    req = urllib.request.Request(url, headers={'User-Agent': 'bbplugin-shapechange'})
    try:
        with _opener.open(req) as resp, open(dest, 'wb') as f:
            f.write(resp.read())
    except Exception as e:
        logger.error('Failed to download %s: %s', url, e)
        raise


def _ensure_jvm():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    required = f'temurin-{_JVM_MAJOR_VERSION}'

    if _JVM_MARKER.exists() and _JVM_MARKER.read_text().strip() == required:
        java = _find_java_exe()
        if java:
            return java

    os_name, arch = _platform_info()
    url = (
        f'https://api.adoptium.net/v3/binary/latest/{_JVM_MAJOR_VERSION}/ga/'
        f'{os_name}/{arch}/jre/hotspot/normal/eclipse'
    )

    archive_ext = '.zip' if os_name == 'windows' else '.tar.gz'
    archive = _CACHE_DIR / f'jre{archive_ext}'

    if _JVM_DIR.exists():
        shutil.rmtree(_JVM_DIR)
    _JVM_DIR.mkdir()

    _download(url, archive)

    if os_name == 'windows':
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(_JVM_DIR)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(_JVM_DIR)
    archive.unlink()

    _JVM_MARKER.write_text(required)
    java = _find_java_exe()
    if not java:
        raise RuntimeError('JRE downloaded but java executable not found')
    return java


def _find_java_exe():
    exe = 'java.exe' if platform.system() == 'Windows' else 'java'
    for path in _JVM_DIR.rglob(exe):
        if path.is_file():
            return str(path)
    return None


def _ensure_shapechange():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if (_SHAPECHANGE_MARKER.exists()
            and _SHAPECHANGE_MARKER.read_text().strip() == _SHAPECHANGE_VERSION
            and _SHAPECHANGE_JAR.exists()):
        return str(_SHAPECHANGE_JAR)

    zip_path = _CACHE_DIR / f'ShapeChange-{_SHAPECHANGE_VERSION}.zip'
    _download(_SHAPECHANGE_ZIP_URL, zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = Path(member).name
            parts = Path(member).parts
            if member.endswith('.jar') and len(parts) == 1:
                # Root-level JAR (thin launcher)
                zf.extract(member, _CACHE_DIR)
            elif len(parts) >= 2 and parts[0] == 'lib' and member.endswith('.jar'):
                # lib/ dependencies
                lib_dir = _CACHE_DIR / 'lib'
                lib_dir.mkdir(exist_ok=True)
                target = lib_dir / name
                target.write_bytes(zf.read(member))

    zip_path.unlink()
    _SHAPECHANGE_MARKER.write_text(_SHAPECHANGE_VERSION)
    return str(_SHAPECHANGE_JAR)


def _is_sqlite3(data: bytes) -> bool:
    return len(data) >= 16 and data[:16] == _SQLITE_MAGIC


class ShapeChangeTransformer:
    """Runs ShapeChange against a SQLite3-based EA model file (.eapx or .qea).

    ``transform_content`` must be a ShapeChange XML configuration. Use the
    ``{input_file}`` and ``{output_dir}`` placeholders where ShapeChange
    should read the model and write its outputs respectively.
    """

    transform_types = ['shapechange']
    default_inputs = ['application/x-ea-eap']
    default_outputs = [{'mimeType': 'application/zip', 'defaultExtension': 'zip'}]

    def transform(self, metadata):
        input_data = metadata.input_data
        if isinstance(input_data, str):
            input_data = input_data.encode('latin-1')

        logger.info('ShapeChange transform starting (input: %d bytes)', len(input_data))

        if not _is_sqlite3(input_data):
            logger.warning(
                'Input is not a SQLite3-based EA model (.eapx/.qea). '
                'The old binary .eap format requires Enterprise Architect and is not supported. '
                'Skipping ShapeChange transform.'
            )
            return None

        logger.info('Input is SQLite3-based EA model, proceeding')
        java = _ensure_jvm()
        logger.info('Using JRE: %s', java)
        jar = _ensure_shapechange()
        logger.info('Using ShapeChange JAR: %s', jar)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            input_file = tmp / 'model.eapx'
            input_file.write_bytes(input_data)
            output_dir = tmp / 'output'
            output_dir.mkdir()

            config = metadata.transform_content
            config = config.replace('{input_file}', str(input_file))
            config = config.replace('{output_dir}', str(output_dir))
            config_file = tmp / 'config.xml'
            config_file.write_text(config, encoding='utf-8')

            logger.info('Running ShapeChange: %s -jar %s -c %s', java, jar, config_file)
            proc = subprocess.run(
                [java, '-jar', jar, '-c', str(config_file)],
                capture_output=True,
                text=True,
                cwd=str(_CACHE_DIR),
            )
            logger.info('ShapeChange exited with code %d', proc.returncode)
            if proc.stdout:
                logger.info('ShapeChange stdout: %s', proc.stdout[:2000])
            if proc.stderr:
                logger.info('ShapeChange stderr: %s', proc.stderr[:2000])

            output_files = [f for f in output_dir.rglob('*') if f.is_file()]
            logger.info('ShapeChange output files: %s', [str(f.relative_to(output_dir)) for f in output_files])

            if proc.returncode != 0 and not output_files:
                raise RuntimeError(
                    f'ShapeChange failed (exit {proc.returncode}):\n'
                    f'{proc.stderr or proc.stdout}'
                )

            if not output_files:
                return None

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(output_files):
                    zf.write(f, f.relative_to(output_dir))

            return buf.getvalue()