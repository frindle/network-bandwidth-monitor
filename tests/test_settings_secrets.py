import unittest

from app.settings_secrets import public_view, updates

KEYS = ['local_subnet', 'cf_api_token', 'cf_zone_id']


class PublicView(unittest.TestCase):
    def test_token_never_returned(self):
        out = public_view({'local_subnet': '10.0.0.0/20', 'cf_api_token': 'secret-token-123', 'cf_zone_id': 'z'})
        self.assertNotIn('cf_api_token', out)
        self.assertNotIn('secret-token-123', repr(out))
        self.assertIs(out['cf_api_token_set'], True)
        self.assertEqual(out['local_subnet'], '10.0.0.0/20')

    def test_unset_flag(self):
        self.assertIs(public_view({})['cf_api_token_set'], False)
        self.assertIs(public_view({'cf_api_token': ''})['cf_api_token_set'], False)


class Updates(unittest.TestCase):
    def test_blank_token_keeps_stored(self):
        self.assertEqual(updates({'cf_api_token': '', 'cf_zone_id': ' z2 '}, KEYS), {'cf_zone_id': 'z2'})
        self.assertEqual(updates({'cf_api_token': None}, KEYS), {})
        self.assertEqual(updates({}, KEYS), {})

    def test_new_token_saved(self):
        self.assertEqual(updates({'cf_api_token': ' new '}, KEYS), {'cf_api_token': 'new'})

    def test_explicit_clear(self):
        self.assertEqual(updates({'cf_api_token': 'x', 'cf_api_token_clear': True}, KEYS), {'cf_api_token': None})
        self.assertEqual(updates({'cf_api_token_clear': 'yes'}, KEYS), {})

    def test_plain_fields_unchanged_behavior(self):
        self.assertEqual(updates({'local_subnet': '', 'cf_zone_id': None}, KEYS), {'local_subnet': None, 'cf_zone_id': None})
        self.assertEqual(updates({'local_subnet': ' 10.0.0.0/20 '}, KEYS), {'local_subnet': '10.0.0.0/20'})


if __name__ == '__main__':
    unittest.main()
