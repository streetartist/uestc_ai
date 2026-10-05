"""Trust only official Cloudflare proxy ranges when restoring visitor IPs."""
import ipaddress
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

ranges = []
for version in (4, 6):
    request = Request(f'https://www.cloudflare.com/ips-v{version}/', headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=20) as response:
        lines = response.read(65536).decode('ascii').splitlines()
    networks = [ipaddress.ip_network(line.strip()) for line in lines if line.strip()]
    assert networks and all(item.version == version and item.prefixlen > 0 for item in networks)
    ranges.extend(networks)
target = Path('/etc/nginx/conf.d/uestc-cloudflare-real-ip.conf')
old = target.read_bytes() if target.exists() else None
target.write_text('# Official Cloudflare proxy ranges; refresh when their ranges change.\n'
                  + ''.join(f'set_real_ip_from {item};\n' for item in ranges)
                  + 'real_ip_header CF-Connecting-IP;\nreal_ip_recursive on;\n')
try:
    subprocess.run(['nginx', '-t'], check=True)
except Exception:
    if old is None:
        target.unlink()
    else:
        target.write_bytes(old)
    raise
subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
print('Cloudflare visitor IP configuration verified:', len(ranges), 'ranges')
