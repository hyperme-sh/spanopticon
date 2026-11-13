"""Generate channel Compose for native owners with existing runtime contracts.

Unlike new_plane.manifests, these owners do not implement Spanopticon's assurance
API. Their supplied runtime configuration remains authoritative; this generator
neither creates credentials nor introduces framework ports or provider settings.
"""
import copy
import re
from service_channels import reference

NAME = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z')


def compose(runtimes, channels, repositories):
    """Render exactly the configured owner set without registry or host access."""
    if (not isinstance(runtimes, dict) or not runtimes
            or not isinstance(channels, dict) or not isinstance(repositories, dict)
            or set(runtimes) != set(channels) or set(channels) != set(repositories)):
        raise ValueError('Runtime, channel and repository sets must match')
    services = {}
    for name, runtime in runtimes.items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > 63:
            raise ValueError('Invalid owner service name')
        if not isinstance(runtime, dict) or any(key in runtime for key in ('build', 'extends', 'provider')):
            raise ValueError('Explicit native owner runtime required')
        service = copy.deepcopy(runtime)
        service['image'] = reference(channels[name], repositories[name])
        service['pull_policy'] = 'always'
        services[name] = service
    return {'services': services}
