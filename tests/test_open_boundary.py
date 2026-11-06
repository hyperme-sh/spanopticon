import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('open_boundary', Path(__file__).parents[1]/'scripts/check_open_boundary.py')
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class BoundaryTests(unittest.TestCase):
    def test_sdk_types_fields_and_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            for name, body in {'sdk.ts':'type Customer = Stripe.Customer',
                               'wire.proto':'string stripe_customer_id = 1;',
                               'pyproject.toml':'dependencies = ["stripe"]',
                               'client.py':'import stripe'}.items():
                (root/name).write_text(body)
            self.assertEqual(len(scanner.violations([root])), 4)

    def test_missing_surface_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaises(ValueError):
                scanner.violations([Path(scratch)/'missing'])

    def test_generic_port_passes(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root/'port.py').write_text('processor_reference: str\n')
            self.assertEqual(scanner.violations([root]), [])

    def test_requirements_and_lockfiles_are_not_skipped(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            files = {
                'requirements.txt': 'stripe==12.0.0\n',
                'requirements-dev.txt': 'fastapi==0.115.0\nStripe==1\n',
                'requirements/base.txt': 'stripe-sdk==3\n',
                'constraints.txt': '-c https://example.invalid/stripe.txt\n',
                'Pipfile.lock': '{"default": {"stripe": {}}}\n',
                'poetry.lock': 'name = "stripe"\n',
                'go.sum': 'github.com/stripe/stripe-go v1 h1:abc\n',
                'go.mod': 'require github.com/stripe/stripe-go v1\n',
                'pom.xml': '<artifactId>stripe-java</artifactId>\n',
                'bun.lockb': None,
                'notes.txt': 'prose about stripe is not a dependency\n',
                'README.md': 'stripe\n',
            }
            for name, body in files.items():
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                if body is None:
                    path.write_bytes(b'\xffstripe\xff')
                else:
                    path.write_text(body)
            found = scanner.violations([root])
            self.assertEqual(len(found), 10)
            joined = '\n'.join(found)
            for name in ('requirements.txt', 'requirements-dev.txt', 'base.txt',
                         'constraints.txt', 'Pipfile.lock', 'poetry.lock', 'go.sum',
                         'go.mod', 'pom.xml', 'bun.lockb'):
                self.assertIn(name, joined)
            self.assertNotIn('notes.txt', joined)
            self.assertNotIn('README.md', joined)

    def test_clean_dependency_metadata_passes(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root/'requirements.txt').write_text('fastapi==0.115.0\n')
            (root/'uv.lock').write_text('name = "fastapi"\n')
            (root/'bun.lockb').write_bytes(b'\xfffastapi\xff')
            self.assertEqual(scanner.violations([root]), [])
            self.assertEqual(scanner.violations([root/'requirements.txt']), [])

    def test_direct_requirements_file_is_scanned(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch)/'requirements.txt'
            path.write_text('stripe==12.0.0\n')
            found = scanner.violations([path])
            self.assertEqual(len(found), 1)
            self.assertIn('requirements.txt:1:', found[0])

    def test_invalid_source_encoding_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch)/'client.py'
            path.write_bytes(b'\xff')
            with self.assertRaises(UnicodeError):
                scanner.violations([path])
