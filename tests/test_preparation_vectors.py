import json
from pathlib import Path
import unittest
from release.preparation_contract import exercise


class PreparationVectorsTest(unittest.TestCase):
    pass


def test_vector(vector):
    def run(self):
        if vector['accepted']:
            result = exercise(vector['case'])
            self.assertIs(result['publication_authorized'], False)
        else:
            with self.assertRaises(ValueError):
                exercise(vector['case'])
    return run


for vector in json.loads((Path(__file__).resolve().parents[1] / 'release/preparation-vectors.json').read_text()):
    setattr(PreparationVectorsTest, 'test_' + vector['case'].replace('-', '_'), test_vector(vector))
