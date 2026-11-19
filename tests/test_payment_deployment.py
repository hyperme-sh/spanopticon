import copy
import json
from pathlib import Path
import unittest
from deployment.payment import FLAGS, render


class PaymentDeployment(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(Path(__file__).with_name('payment-owner-plan.json').read_text())

    def test_settings_and_secrets_match_all_three_targets(self):
        compose, resources, bindings = render(self.plan)
        owners = {**self.plan['apps'], 'payment-worker': self.plan['workers']['payment'],
                  **self.plan['payment_events']['relays']}
        deployments = {x['metadata']['name']: x for x in resources if x['kind'] == 'Deployment'}
        for role, spec in owners.items():
            service = compose['services'][role]
            container = deployments[role]['spec']['template']['spec']['containers'][0]
            kube_env = {v['name']: v for v in container['env']}
            self.assertEqual(set(spec['env']), set(service['environment']))
            self.assertEqual(set(spec['env']), set(kube_env))
            self.assertNotIn('ports', service)
            for key, setting in spec['env'].items():
                if 'value' in setting:
                    self.assertEqual(setting['value'], kube_env[key]['value'])
                    self.assertEqual(setting['value'], service['environment'][key])
                else:
                    alias = kube_env[key]['valueFrom']['secretKeyRef']['name']
                    self.assertEqual(spec['secrets'][setting['secret_name']]['key_vault_secret_id'],
                                     bindings['secrets'][alias]['key_vault_secret_id'])
            if 'command' in spec:
                self.assertEqual(spec['command'], container['command'])
                self.assertEqual(spec.get('args', []), service['command'])
        self.assertEqual(self.plan['payment_events']['receiver_maps'], bindings['receiver_maps'])

    def test_refuses_each_billing_switch(self):
        for owner in [self.plan['apps']['payment'], self.plan['workers']['payment']]:
            for flag in FLAGS:
                owner['env'][flag]['value'] = 'true'
                with self.assertRaisesRegex(ValueError, 'billing'):
                    render(self.plan)
                owner['env'][flag]['value'] = 'false'

    def test_refuses_activation_and_missing_relay(self):
        self.plan['active'] = True
        with self.assertRaises(ValueError): render(self.plan)
        self.plan['active'] = False
        del self.plan['payment_events']['relays']['payment-paid-relay']
        with self.assertRaises(ValueError): render(self.plan)

    def test_file_targets_and_no_stripe_key_in_relays(self):
        compose, resources, bindings = render(self.plan)
        for role, spec in self.plan['payment_events']['relays'].items():
            service = compose['services'][role]
            self.assertEqual({x['target'] for x in service['secrets']},
                             {x['mount_path'] + '/' + x['filename'] for x in spec['secret_files'].values()})
            self.assertFalse(any('stripe' in x['source'] for x in service['secrets']))
        self.assertEqual({'payment', 'plane', 'user'},
                         {x['metadata']['name'] for x in resources if x['kind'] == 'Service'})

    def test_compose_does_not_interpolate_literal_settings(self):
        self.plan['apps']['payment']['env']['EXAMPLE'] = {'value': '${SECRET}'}
        compose, _, _ = render(self.plan)
        self.assertEqual('$${SECRET}', compose['services']['payment']['environment']['EXAMPLE'])


if __name__ == '__main__': unittest.main()
