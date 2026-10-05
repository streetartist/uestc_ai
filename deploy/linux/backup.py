"""Daily private PostgreSQL and upload backups, with seven-day retention."""
import os
import subprocess
import tarfile
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

config = {}
for line in Path('/etc/uestc-ai/backend.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        key, value = line.split('=', 1)
        config[key] = value
uri = urlsplit(config['DATABASE_URL'].replace('postgresql+psycopg:', 'postgresql:'))
root = Path('/opt/uestc-ai/shared/backups')
root.mkdir(mode=0o700, exist_ok=True)
root.chmod(0o700)
folder = root / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
folder.mkdir(mode=0o700)
env = {**os.environ, 'PGPASSWORD': uri.password}
subprocess.run(['pg_dump', '-h', uri.hostname, '-p', str(uri.port or 5432), '-U', uri.username,
                '-d', uri.path.lstrip('/'), '-Fc', '-f', str(folder/'database.dump')], env=env, check=True)
with tarfile.open(folder/'uploads.tar.gz', 'w:gz') as archive:
    archive.add('/opt/uestc-ai/shared/uploads', arcname='uploads')
with tarfile.open(folder/'configuration.tar.gz', 'w:gz') as archive:
    archive.add('/etc/uestc-ai', arcname='uestc-ai')
# Only complete backups are eligible for retention. Interrupted backups are retained for diagnosis.
(folder/'COMPLETE').touch()
for old in root.iterdir():
    if old.is_dir() and (old/'COMPLETE').exists() and old.stat().st_mtime < time.time()-7*86400:
        shutil.rmtree(old)
print('Backup completed:', folder.name)
