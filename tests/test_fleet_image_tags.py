"""Fleet selector contracts only; engines and registries are not started."""
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class FleetImageTags(unittest.TestCase):
    def test_compose_owners_follow_independent_test_channels(self):
        for owner in ('gateway', 'plane'):
            document = yaml.safe_load((ROOT / f'fleet/{owner}/compose.yaml').read_text())
            rows = [v for v in document['services'].values() if f'hypermesh-{owner}/runtime' in v.get('image', '')]
            self.assertTrue(rows)
            for row in rows:
                self.assertEqual(row['image'], f'${{{owner.upper()}_IMAGE:-ghcr.io/hyperme-sh/hypermesh-{owner}/runtime:test}}')
        self.assertIn('postgres:17@sha256:', (ROOT/'fleet/plane/compose.yaml').read_text())

    def test_helm_defaults_and_qualification_overrides_are_independent(self):
        for owner in ('gateway', 'plane'):
            value=yaml.safe_load((ROOT/f'fleet/{owner}/chart/values.yaml').read_text())
            self.assertEqual(value['imageReference'],f'ghcr.io/hyperme-sh/hypermesh-{owner}/runtime:test')
            sources=''.join(p.read_text() for p in (ROOT/f'fleet/{owner}/chart/templates').glob('*.yaml'))
            self.assertIn('image: {{ .Values.imageReference }}',sources)
            self.assertNotIn('spanopticon-hypermesh-',sources)
        script=(ROOT/'scripts/check_published_deployment.sh').read_text()
        for owner in ('GATEWAY','PLANE'):
            self.assertIn(f'export {owner}_IMAGE="$image:$CANDIDATE"',script)
            self.assertIn(f'--set-string "imageReference=${owner}_IMAGE"',script)
        self.assertIn("'.destination+\":\"+$tag'",script)
        self.assertNotIn('.manifest_digest',script)
        self.assertNotIn('.config_digest',script)
        self.assertNotIn('cmp -s',script)


if __name__ == '__main__':
    unittest.main()
