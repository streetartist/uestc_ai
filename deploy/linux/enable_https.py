"""Install HTTPS after certbot has issued both domain names."""
import subprocess
from pathlib import Path

cert = '/etc/letsencrypt/live/uestcai.top'
assert Path(cert, 'fullchain.pem').is_file(), 'Issue the certificate first'
tls = f'''ssl_certificate {cert}/fullchain.pem;
    ssl_certificate_key {cert}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:uestc_tls:10m;
    ssl_session_timeout 1d;'''
source = Path(__file__).with_name('nginx.conf').read_text()
source = source.replace('listen 80;', 'listen 443 ssl;').replace('listen [::]:80;', 'listen [::]:443 ssl;')
source = source.replace('server_name uestcai.top www.uestcai.top 47.84.83.142;', 'server_name uestcai.top;\n    ' + tls)
source += f'''
server {{
    listen 80;
    listen [::]:80;
    server_name uestcai.top www.uestcai.top 47.84.83.142;
    location ^~ /.well-known/acme-challenge/ {{ root /var/www/letsencrypt; }}
    location / {{ return 301 https://uestcai.top$request_uri; }}
}}
server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name www.uestcai.top;
    {tls}
    return 301 https://uestcai.top$request_uri;
}}
'''
target = Path('/etc/nginx/sites-available/uestc-ai')
old = target.read_text()
target.write_text(source)
try:
    subprocess.run(['nginx', '-t'], check=True)
except Exception:
    target.write_text(old)
    raise
subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
hook = Path('/etc/letsencrypt/renewal-hooks/deploy/uestc-ai-nginx')
hook.write_text('#!/bin/sh\nnginx -t && systemctl reload nginx\n')
hook.chmod(0o755)
subprocess.run(['systemctl', 'enable', '--now', 'certbot.timer'], check=True)
print('HTTPS_ENABLED')
