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
