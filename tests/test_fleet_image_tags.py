"""Fleet selector contracts only; engines and registries are not started."""
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class FleetImageTags(unittest.TestCase):
    def test_compose_owner_images_require_an_explicit_published_tag(self):
        for owner in ('gateway', 'plane'):
            document = yaml.safe_load((ROOT / f'fleet/{owner}/compose.yaml').read_text())
            rows = [v for v in document['services'].values() if 'spanopticon-hypermesh-' in v.get('image', '')]
            self.assertTrue(rows)
            for row in rows:
                self.assertEqual(row['image'], f'ghcr.io/hyperme-sh/spanopticon-hypermesh-{owner}:${{SPANOPTICON_TEST_TAG:?Set a published test tag}}')
        self.assertIn('postgres:17@sha256:', (ROOT/'fleet/plane/compose.yaml').read_text())

    def test_helm_requires_tag_and_published_comparison_uses_it(self):
        for owner in ('gateway', 'plane'):
            self.assertEqual(yaml.safe_load((ROOT/f'fleet/{owner}/chart/values.yaml').read_text())['imageTag'], '')
            images = []
            for path in (ROOT/f'fleet/{owner}/chart/templates').glob('*.yaml'):
                images += re.findall(r'image: (ghcr.io/hyperme-sh/spanopticon[^\n]+)', path.read_text())
            self.assertTrue(images)
            self.assertTrue(all('.Values.imageTag' in value and '@sha256:' not in value for value in images))
        script=(ROOT/'scripts/check_published_deployment.sh').read_text()
        self.assertIn('export SPANOPTICON_TEST_TAG="$CANDIDATE"', script)
        self.assertEqual(script.count('--set-string "imageTag=$CANDIDATE"'), 2)
        self.assertIn("'.destination+\":\"+$tag'", script)
        self.assertNotIn('.manifest_digest', script)
        self.assertNotIn('.config_digest', script)
        self.assertNotIn('cmp -s', script)


if __name__ == '__main__':
    unittest.main()
