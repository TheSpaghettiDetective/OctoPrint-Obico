import unittest

from octoprint_obico.client_conn import ClientConn


def connect_1x(port=None, baudrate=None, profile=None, *args, **kwargs):
    """Signature of Printer.connect() in OctoPrint 1.x."""


def connect_2x(connector=None, parameters=None, profile=None, *args, **kwargs):
    """Signature of Printer.connect() in OctoPrint 2.0.0."""


class PrinterConnectArgsTests(unittest.TestCase):

    def setUp(self):
        self.conn = ClientConn(plugin=None)

    def test_octoprint_1x_call_is_unchanged(self):
        self.assertEqual(
            self.conn.adapt_printer_connect_args(connect_1x, ['/dev/ttyOcto4a', ''], {}),
            (['/dev/ttyOcto4a', ''], {}))

    def test_octoprint_2x_auto_baudrate(self):
        self.assertEqual(
            self.conn.adapt_printer_connect_args(connect_2x, ['/dev/ttyOcto4a', ''], {}),
            ([], {'connector': 'serial', 'parameters': {'port': '/dev/ttyOcto4a', 'baudrate': 0}, 'profile': None}))

    def test_octoprint_2x_explicit_baudrate_and_profile(self):
        self.assertEqual(
            self.conn.adapt_printer_connect_args(connect_2x, ['VIRTUAL', '115200', 'prusa'], {}),
            ([], {'connector': 'serial', 'parameters': {'port': 'VIRTUAL', 'baudrate': 115200}, 'profile': 'prusa'}))

    def test_octoprint_2x_new_style_call_is_unchanged(self):
        kwargs = {'connector': 'serial', 'parameters': {'port': 'VIRTUAL'}}
        self.assertEqual(self.conn.adapt_printer_connect_args(connect_2x, [], kwargs), ([], kwargs))


if __name__ == '__main__':
    unittest.main()
