"""Create an internal agent network exposing only the host's model proxy."""
import json
import subprocess

NETWORK = 'uestc-evaluation'
BRIDGE = 'br-uestc-eval'
SUBNET = '172.30.44.0/24'
GATEWAY = '172.30.44.1'

def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

result = subprocess.run(['docker', 'network', 'inspect', NETWORK], capture_output=True, text=True)
if result.returncode:
    run(['docker', 'network', 'create', '--internal', '--subnet', SUBNET, '--gateway', GATEWAY,
         '--opt', 'com.docker.network.bridge.name=' + BRIDGE,
         '--label', 'uestc.managed=evaluation', NETWORK])
else:
    network = json.loads(result.stdout)[0]
    assert network['Internal'] and not network['EnableIPv6']
    assert network['Options'].get('com.docker.network.bridge.name') == BRIDGE
    assert network['IPAM']['Config'] == [{'Subnet': SUBNET, 'IPRange': '', 'Gateway': GATEWAY}] or (
        len(network['IPAM']['Config']) == 1 and network['IPAM']['Config'][0]['Subnet'] == SUBNET
        and network['IPAM']['Config'][0]['Gateway'] == GATEWAY)

rules = [
    ['INPUT', '-i', BRIDGE, '-s', SUBNET, '-d', GATEWAY, '-p', 'tcp', '--dport', '8080', '-j', 'ACCEPT'],
    ['INPUT', '-i', BRIDGE, '-j', 'DROP'],
    ['DOCKER-USER', '-i', BRIDGE, '-j', 'DROP'],
]
# Insert in reverse order so the proxy exception is ahead of the host DROP.
for rule in reversed(rules):
    found = subprocess.run(['iptables', '-C', *rule], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if found.returncode:
        run(['iptables', '-I', rule[0], '1', *rule[1:]])
print('Internal evaluation network and host access rules verified')
