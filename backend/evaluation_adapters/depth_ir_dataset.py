"""Version-2 depth/IR datasets: ordered original images in each clip directory."""
from pathlib import Path
import hashlib
import re
import shutil

EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}


def natural_key(path):
    fallback = tuple((0, int(p)) if p.isdigit() else (1, p.lower()) for p in re.split(r'(\d+)', path.name))
    match = re.search(r'_(\d+)(?:_Color)?\.[^.]+$', path.name, re.IGNORECASE)
    return (0, int(match.group(1)), fallback) if match else (1, 0, fallback)


def modality_files(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Missing modality directory')
    files = sorted(directory.iterdir(), key=natural_key)
    if not files:
        raise ValueError('Empty modality directory')
    for path in files:
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            raise ValueError('Modality directory must contain regular image files only')
    return files


def paired_files(directory):
    """Pair matching source frame numbers before anonymous filenames are assigned."""
    files = {m: modality_files(Path(directory) / m) for m in ('Depth_Color', 'IR')}
    indexes = {}
    for modality, paths in files.items():
        indexes[modality] = []
        for path in paths:
            match = re.search(r'(\d+)(?:_Color)?\.[^.]+$', path.name, re.IGNORECASE)
            if not match:
                raise ValueError('Every image filename must end in a frame number')
            indexes[modality].append(int(match.group(1)))
        if len(set(indexes[modality])) != len(paths):
            raise ValueError('Duplicate frame number')
    if indexes['Depth_Color'] != indexes['IR']:
        raise ValueError('Depth_Color and IR frame numbers must match')
    return files


def clip_digest(directory, verify_images=False):
    if verify_images:
        from PIL import Image
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()} != {'Depth_Color', 'IR'}:
        raise ValueError('Clip directory needs exactly Depth_Color and IR')
    digest = hashlib.sha256()
    files = paired_files(directory)
    sizes = {}
    for modality in ('Depth_Color', 'IR'):
        for index, path in enumerate(files[modality]):
            digest.update(path.relative_to(directory).as_posix().encode('utf-8') + b'\0')
            content = hashlib.sha256()
            with path.open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    content.update(block)
            digest.update(content.digest())
            if verify_images:
                with Image.open(path) as image:
                    expected_mode = 'RGB' if modality == 'Depth_Color' else 'L'
                    if image.mode != expected_mode:
                        raise ValueError('Depth_Color must be RGB pseudocolor; IR must be 8-bit L')
                    if modality == 'Depth_Color':
                        sizes[index] = image.size
                    elif image.size != sizes[index]:
                        raise ValueError('Paired Depth_Color and IR image sizes must match')
                    image.verify()
    return digest.hexdigest()


def validate_manifest(directory, data, on_sample=None):
    if (not isinstance(data, dict) or set(data) != {'version', 'classes', 'modality', 'samples'} or data['version'] != 2
            or data['modality'] != 'depth_ir' or not isinstance(data['classes'], list)
            or len(data['classes']) != 40
            or any(not isinstance(c, str) or not 1 <= len(c) <= 100 for c in data['classes'])
            or len(set(data['classes'])) != 40
            or not isinstance(data['samples'], list) or not 1 <= len(data['samples']) <= 100000):
        raise ValueError('Expected version-2 depth_ir manifest with 40 classes')
    seen = set()
    for sample in data['samples']:
        if (not isinstance(sample, dict) or set(sample) != {'file', 'label', 'sha256'} or type(sample['label']) is not int
                or not 0 <= sample['label'] < 40 or not isinstance(sample['file'], str)
                or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', sample['file']) or sample['file'] in seen
                or not isinstance(sample['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', sample['sha256'])):
            raise ValueError('Invalid depth_ir sample metadata')
        seen.add(sample['file'])
        if clip_digest(Path(directory) / sample['file'], verify_images=True) != sample['sha256']:
            raise ValueError('Depth/IR clip SHA256 mismatch')
        if on_sample:
            on_sample(len(seen), len(data['samples']))
    return data


def copy_clip_anonymous(source, target, *, file_mode=0o644, directory_mode=0o755):
    """Copy only image bytes, preserving temporal order, stripping source names."""
    source, target = Path(source), Path(target)
    files = paired_files(source)
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(mode=directory_mode)
    target.chmod(directory_mode)
    for modality in ('Depth_Color', 'IR'):
        destination = target / modality
        destination.mkdir(mode=directory_mode)
        destination.chmod(directory_mode)
        for index, frame in enumerate(files[modality]):
            name = destination / f'{index:06d}{frame.suffix.lower()}'
            shutil.copyfile(frame, name)
            name.chmod(file_mode)


def expose_sample(dataset, sample, exposed, version):
    target = Path(exposed) / ('clip' if version == 2 else 'clip.npy')
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    if version == 2:
        copy_clip_anonymous(Path(dataset) / sample['file'], target)
    else:
        shutil.copyfile(Path(dataset) / sample['file'], target)
        target.chmod(0o644)
    return target
