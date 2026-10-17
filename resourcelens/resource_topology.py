"""Read only relationships exported by sysfs; missing topology stays unknown."""
from pathlib import Path
import re
from .hardware import read, cpulist


def topology(sys):
    sys = Path(sys)
    result = dict(memory_nodes=[], disks={}, network={}, gpus={})
    def integer(path):
        value = read(path)
        return int(value) if value.isdigit() else None
    def attachment(device):
        pci = next((part for part in reversed(device.resolve().parts)
                    if re.fullmatch(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]', part)), None)
        return dict(pci=pci, numa=integer(device / 'numa_node'))
    for node in sorted((sys / 'devices/system/node').glob('node[0-9]*')):
        values = {key: int(value) * 1024 for key, value in
                  re.findall(r'(MemTotal|MemFree):\s+(\d+)\s+kB', read(node / 'meminfo'))}
        result['memory_nodes'].append(dict(id=node.name, cpus=cpulist(read(node / 'cpulist')),
            total=values.get('MemTotal'), free=values.get('MemFree')))
    for disk in (sys / 'block').glob('*'):
        if disk.name.startswith(('loop', 'ram')):
            continue
        sectors = integer(disk / 'size')
        result['disks'][disk.name] = dict(capacity=sectors * 512 if sectors is not None else None,
            backing=sorted(p.name for p in (disk / 'slaves').glob('*')),
            holders=sorted(p.name for p in (disk / 'holders').glob('*')),
            partitions=sorted(p.name for p in disk.glob('*') if (p / 'partition').exists()),
            **attachment(disk / 'device'))
    for dev in (sys / 'class/net').glob('*'):
        result['network'][dev.name] = dict(master=(dev / 'master').resolve().name if (dev / 'master').exists() else None,
            lower=sorted(p.name[6:] for p in dev.glob('lower_*')),
            **attachment(dev / 'device'))
    for card in (sys / 'class/drm').glob('card*'):
        if re.fullmatch(r'card\d+', card.name):
            result['gpus'][card.name] = attachment(card / 'device')
    return result
