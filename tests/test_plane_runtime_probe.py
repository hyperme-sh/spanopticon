import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
spec=importlib.util.spec_from_file_location('plane_qualification',Path(__file__).parents[1]/'scripts/qualify_plane_runtime.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def response(value):
    result=io.BytesIO(json.dumps(value).encode());result.status=200;return result

class PlaneProbeTests(unittest.TestCase):
    def test_wrong_deployed_source_is_rejected(self):
        with patch.object(module.urllib.request,'urlopen',return_value=response({'git_sha':'wrong'})):
            with self.assertRaises(AssertionError):module.probe('http://localhost','expected')

    def test_tenant_header_authorization_bypass_fails_qualification(self):
        with patch.object(module.urllib.request,'urlopen',side_effect=[response({'git_sha':'expected'}),response(['class']),response({'allowed':True})]):
            with self.assertRaises(AssertionError):module.probe('http://localhost','expected')

    def test_financial_route_must_be_absent(self):
        denied=HTTPError('http://localhost',403,'denied',{},None)
        with patch.object(module.urllib.request,'urlopen',side_effect=[response({'git_sha':'expected'}),response(['class']),denied,response({'allocated':True})]):
            with self.assertRaises(AssertionError):module.probe('http://localhost','expected')
