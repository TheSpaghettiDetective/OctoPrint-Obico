import copy
import importlib.util
import os
import unittest


MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'octoprint_obico',
    'redaction.py',
)
SPEC = importlib.util.spec_from_file_location('obico_redaction', MODULE_PATH)
redaction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(redaction)


class DummyRequest(object):
    method = 'GET'
    url = 'https://camera:camera-password@example.com/live?token=url-secret&fps=10'
    headers = {
        'Authorization': 'Token header-secret',
        'cOoKiE': 'session=cookie-secret',
        'Accept': 'application/json',
    }
    body = 'password=body-secret'


class RedactionTests(unittest.TestCase):

    def test_plugin_settings_auth_token_is_redacted_without_losing_safe_values(self):
        settings = {
            'auth_token': 'settings-secret',
            'endpoint_prefix': 'https://app.obico.io',
            'webcams': [{'name': 'classic', 'target_fps': 15}],
        }

        result = redaction.redact_sensitive_data(settings)

        self.assertEqual(redaction.REDACTED, result['auth_token'])
        self.assertEqual('https://app.obico.io', result['endpoint_prefix'])
        self.assertEqual('classic', result['webcams'][0]['name'])
        self.assertEqual(15, result['webcams'][0]['target_fps'])

    def test_authorization_and_cookie_headers_are_case_insensitively_redacted(self):
        result = redaction.redact_sensitive_data(DummyRequest.headers)

        self.assertEqual(redaction.REDACTED, result['Authorization'])
        self.assertEqual(redaction.REDACTED, result['cOoKiE'])
        self.assertEqual('application/json', result['Accept'])

    def test_url_credentials_and_sensitive_query_values_are_redacted(self):
        result = redaction.redact_url(
            'https://camera:password@example.com/live?TOKEN=secret&api_key=key-secret&fps=10&quality=high'
        )

        self.assertEqual(
            'https://<redacted>:<redacted>@example.com/live?TOKEN=<redacted>&api_key=<redacted>&fps=10&quality=high',
            result,
        )

    def test_redaction_does_not_modify_nested_input(self):
        original = {
            'auth_token': 'secret',
            'nested': [{'password': 'nested-secret', 'safe': 'readable'}],
        }
        snapshot = copy.deepcopy(original)

        redaction.redact_sensitive_data(original)

        self.assertEqual(snapshot, original)

    def test_http_request_keeps_diagnostics_but_never_logs_body(self):
        result = redaction.format_http_request(DummyRequest())

        self.assertIn('GET', result)
        self.assertIn('example.com/live', result)
        self.assertIn('fps=10', result)
        self.assertIn("'Accept': 'application/json'", result)
        self.assertNotIn('camera-password', result)
        self.assertNotIn('url-secret', result)
        self.assertNotIn('header-secret', result)
        self.assertNotIn('cookie-secret', result)
        self.assertNotIn('body-secret', result)

    def test_embedded_url_keeps_safe_query_values_and_redacts_header_lines(self):
        result = redaction.redact_text(
            'ffmpeg -i https://cam/live?token=secret&quality=high Authorization: Bearer header-secret'
        )

        self.assertIn('quality=high', result)
        self.assertNotIn('secret', result)
        self.assertIn('Authorization: <redacted>', result)

    def test_user_id_and_ordinary_diagnostic_identifiers_remain_visible(self):
        data = {
            'user_id': 'user-123',
            'userId': 'user-456',
            'session_id': 987654,
            'sessionId': 123456,
            'code': 503,
            'key': 'temperature',
            'public_key': 'public-material',
            'agent_signature': 'md5:file-fingerprint',
            'rotation': 90,
        }

        self.assertEqual(data, redaction.redact_sensitive_data(data))

    def test_separator_delimited_token_header_and_query_names_are_redacted(self):
        headers = {
            'X-Amz-Security-Token': 'header-secret',
            'xAmzSecurityToken': 'camel-header-secret',
            'X-Request-ID': 'request-123',
        }
        url = 'https://example.com/path?X-Amz-Security-Token=query-secret&action=stream'

        self.assertEqual(redaction.REDACTED, redaction.redact_sensitive_data(headers)['X-Amz-Security-Token'])
        self.assertEqual(redaction.REDACTED, redaction.redact_sensitive_data(headers)['xAmzSecurityToken'])
        self.assertEqual('request-123', redaction.redact_sensitive_data(headers)['X-Request-ID'])
        self.assertEqual(
            'https://example.com/path?X-Amz-Security-Token=<redacted>&action=stream',
            redaction.redact_url(url),
        )

    def test_serialized_token_assignments_are_redacted_without_hiding_safe_fields(self):
        json_body = '{"token":"json-secret","code":503,"user_id":"user-123"}'
        form_body = 'token=form-secret&code=503&user_id=user-123'

        redacted_json = redaction.redact_text(json_body)
        redacted_form = redaction.redact_text(form_body)

        self.assertNotIn('json-secret', redacted_json)
        self.assertNotIn('form-secret', redacted_form)
        self.assertIn('"code":503', redacted_json)
        self.assertIn('user_id=user-123', redacted_form)

    def test_http_tunnel_bodies_are_omitted_and_safe_metadata_remains_visible(self):
        message = {
            'http.tunnel': {
                'method': 'POST',
                'path': '/api/example',
                'params': {'token': 'query-secret', 'page': '2'},
                'headers': {'Authorization': 'Bearer header-secret', 'Accept': 'application/json'},
                'data': '{"token":"body-secret"}',
                'response': {'status': 200, 'content': 'response-secret'},
            },
        }

        result = redaction.redact_sensitive_data(message)['http.tunnel']

        self.assertEqual(redaction.REDACTED, result['data'])
        self.assertEqual(redaction.REDACTED, result['response']['content'])
        self.assertEqual(redaction.REDACTED, result['params']['token'])
        self.assertEqual(redaction.REDACTED, result['headers']['Authorization'])
        self.assertEqual('2', result['params']['page'])
        self.assertEqual('application/json', result['headers']['Accept'])
        self.assertEqual('/api/example', result['path'])

    def test_malformed_urls_and_unprintable_values_do_not_raise(self):
        class Unprintable(object):
            def __str__(self):
                raise ValueError('cannot serialize')

            def __repr__(self):
                raise ValueError('cannot serialize')

        malformed = 'http://[invalid-host/path?password=secret&mode=debug'
        encoded_name = 'http://[invalid-host/path?to%6ben=encoded-secret&mode=debug'
        result = redaction.redact_url(malformed)
        encoded_result = redaction.redact_url(encoded_name)

        self.assertNotIn('secret', result)
        self.assertNotIn('encoded-secret', encoded_result)
        self.assertIn('mode=debug', result)
        self.assertIn('mode=debug', encoded_result)
        self.assertIn('unprintable', redaction.redact_sensitive_data(Unprintable()))


if __name__ == '__main__':
    unittest.main()
