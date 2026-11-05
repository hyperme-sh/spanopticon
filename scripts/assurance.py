"""Deployment assurance selection; configuration is not provider acceptance."""
import json
import re
from urllib.parse import urlsplit

MODES = {'stripe-kyc': ('stripe_kyc',), 'verification': ('verification',),
         'both': ('stripe_kyc', 'verification')}


def settings(config):
    if not isinstance(config, dict) or set(config) != {'mode', 'providers'}:
        raise ValueError('assurance requires mode and providers')
    mode = config['mode']
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('select stripe-kyc, verification, or both')
    providers = config['providers']
    if not isinstance(providers, dict) or set(providers) != set(MODES[mode]):
        raise ValueError('configure exactly the providers selected by assurance mode')
    environment = {'SPANOPTICON_ASSURANCE_MODE': mode}
    secrets = {}
    for name, provider in providers.items():
        if not isinstance(provider, dict) or set(provider) != {'url', 'credential_secret'}:
            raise ValueError('provider requires url and credential_secret')
        url, secret = provider['url'], provider['credential_secret']
        if not isinstance(url, str):
            raise ValueError('provider URL must be HTTPS')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(c.isspace() for c in url)):
            raise ValueError('provider URL must be HTTPS without credentials, query or fragment')
        try:
            parsed.port
        except ValueError as error:
            raise ValueError('invalid provider port') from error
        if not isinstance(secret, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,61}[a-z0-9]|[a-z]', secret):
            raise ValueError('credential_secret must be a lowercase DNS label')
        prefix = 'SPANOPTICON_' + name.upper()
        environment[prefix + '_URL'] = url
        environment[prefix + '_TOKEN_FILE'] = '/run/secrets/' + secret
        secrets[secret] = True
    return environment, secrets


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate assurance configuration key')
            result[key] = value
        return result
    config = json.loads(path.read_text(), object_pairs_hook=unique)
    settings(config)
    return config
