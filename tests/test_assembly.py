import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from deployment import bundle as b
from runtime_fixture import runtime_fixture

spec=importlib.util.spec_from_file_location('assemble',Path(__file__).resolve().parents[1]/'scripts/assemble_test_bundle.py')
assembler=importlib.util.module_from_spec(spec);spec.loader.exec_module(assembler)


class AssemblyTests(unittest.TestCase):
    def test_roundtrip_preserves_existing_deployment_asset_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture,_,_=runtime_fixture(root/'source')
            metadata={k:v for k,v in fixture.bundle.items() if k!='deployment_assets'}
            files={n:fixture.path(d) for n,d in fixture.bundle['deployment_assets'].items()}
            reference=assembler.assemble(metadata,files['compose'],files['helm'],'ghcr.io/example/plane',root/'assembled')
            lock=b.verify(root/'assembled',reference)
            self.assertEqual(lock['deployment_assets'],fixture.bundle['deployment_assets'])
            self.assertEqual(lock['components'],fixture.bundle['components'])
            self.assertTrue((root/'assembled/index.json').exists())
            self.assertFalse((root/'assembled/selection.json').exists())
            with self.assertRaises(FileExistsError):assembler.assemble(metadata,files['compose'],files['helm'],'ghcr.io/example/plane',root/'assembled')

    def test_bad_metadata_does_not_produce_completed_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture,_,_=runtime_fixture(root/'source')
            metadata={k:v for k,v in fixture.bundle.items() if k!='deployment_assets'};metadata['candidate']='0.24.0'
            files={n:fixture.path(d) for n,d in fixture.bundle['deployment_assets'].items()}
            with self.assertRaises(ValueError):assembler.assemble(metadata,files['compose'],files['helm'],'ghcr.io/example/plane',root/'assembled')
            self.assertFalse((root/'assembled/deployment.lock.json').exists())
