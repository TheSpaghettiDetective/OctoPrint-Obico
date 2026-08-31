import importlib.util
import os
import unittest


MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'octoprint_obico',
    'webrtc_diagnostics.py',
)
SPEC = importlib.util.spec_from_file_location('obico_webrtc_diagnostics', MODULE_PATH)
diagnostics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostics)


class WebRTCDiagnosticsTests(unittest.TestCase):

    def test_interval_bitrate_uses_wall_clock_byte_delta(self):
        self.assertEqual(800.0, diagnostics.interval_bitrate_kbps(600000, 100000, 5))

    def test_interval_bitrate_rejects_missing_reset_or_invalid_samples(self):
        self.assertIsNone(diagnostics.interval_bitrate_kbps('N/A', 100000, 5))
        self.assertIsNone(diagnostics.interval_bitrate_kbps(90000, 100000, 5))
        self.assertIsNone(diagnostics.interval_bitrate_kbps(100000, 100000, 0))

    def test_janus_stats_keep_media_counters_but_remove_network_identifiers(self):
        handle_info = {
            'ice-mode': 'full',
            'queued-packets': 2,
            'sdps': {'local': 'sensitive SDP'},
            'streams': [{
                'mindex': 0,
                'type': 'video',
                'components': [{
                    'state': 'ready',
                    'selected-pair': '192.0.2.1:1234 <-> 198.51.100.1:5678',
                    'out_stats': {
                        'video': {'bytes': 123456, 'bytes_lastsec': 25000, 'packets': 120, 'nacks': 3},
                    },
                }],
            }],
        }

        result = diagnostics.extract_janus_diagnostic_stats(handle_info)

        self.assertEqual('full', result['ice-mode'])
        self.assertEqual(25000, result['streams'][0]['components'][0]['out_stats']['video']['bytes_lastsec'])
        self.assertEqual(200.0, result['streams'][0]['components'][0]['out_stats']['video']['bitrate_kbps'])
        self.assertNotIn('sdps', result)
        self.assertNotIn('selected-pair', result['streams'][0]['components'][0])
        self.assertNotIn('192.0.2.1', repr(result))

    def test_legacy_janus_video_stats_are_converted_to_kbps(self):
        handle_info = {
            'webrtc': {'streams': [{
                'rtcp_stats': {
                    'video': {'rtt': 42, 'lost': 2, 'lost-by-remote': 5, 'out-link-quality': 83},
                },
                'components': [{
                    'out_stats': {
                        'video_packets': 120,
                        'video_bytes': 123456,
                        'video_bytes_lastsec': 25000,
                        'video_nacks': 3,
                    },
                }],
            }]},
        }

        result = diagnostics.extract_janus_diagnostic_stats(handle_info)
        stream = result['webrtc']['streams'][0]
        video = stream['components'][0]['out_stats']

        self.assertEqual(25000, video['video_bytes_lastsec'])
        self.assertEqual(200.0, video['video_bitrate_kbps'])
        self.assertEqual(3, video['video_nacks'])
        self.assertEqual(42, stream['rtcp_stats']['video']['rtt'])
        self.assertEqual(5, stream['rtcp_stats']['video']['lost-by-remote'])


if __name__ == '__main__':
    unittest.main()
