import requests
import urllib.parse
import logging
import threading

from .ws import WebSocketClient

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')


class LocalProxy(object):

    def __init__(self, base_url, on_http_response, on_ws_message):
        self.base_url = base_url
        self.on_http_response = on_http_response
        self.on_ws_message = on_ws_message
        self.ref_to_ws = {}

    def send_http_to_local(
            self, ref, method, path,
            params=None, data=None, headers=None, timeout=30):

        throwing = False
        url = urllib.parse.urljoin(self.base_url, path)

        try:
            resp = getattr(requests, method)(
                url, params=params, headers=headers, data=data,
                timeout=timeout,
                allow_redirects=True)
            resp_data = {
                'status': resp.status_code,
                'content': resp.content,
                'headers': {k: v for k, v in resp.headers.items()},
            }
        except Exception as ex:
            throwing = True
            resp_data = {
                'status': 502,
                'content': ex.message,
                'headers': {}
            }

        self.on_http_response(
            {'http.proxy': {'ref': ref, 'response': resp_data}},
            throwing=throwing,
            as_binary=True)
        return

    def send_ws_to_local(self, ref, path, data):
        if ref not in self.ref_to_ws:
            self.connect_ws(ref, path)

        ws = self.ref_to_ws[ref]
        if isinstance(data, bytes):
            ws.send_binary(data)
        else:
            ws.send_text(data)

    def connect_ws(self, ref, path):
        def on_ws_error(ws, ex):
            _logger.error("Proxy WS error %s", ex)

        def on_ws_close(ws):
            _logger.error("Proxy WS is closing")
            del self.ref_to_ws[ref]

        def on_ws_msg(ws, data):
            self.on_ws_message(
                {'ws.proxy': {'ref': ref, 'data': data}},
                throwing=False,
                as_binary=True)

        url = urllib.parse.urljoin(self.base_url, path)
        url = url.replace('http://', 'ws://')
        url = url.replace('https://', 'wss://')

        ws = WebSocketClient(
            url,
            token=None,
            on_ws_msg=on_ws_msg,
            on_ws_close=on_ws_close
        )
        self.ref_to_ws[ref] = ws

        wst = threading.Thread(target=ws.run)
        wst.daemon = True
        wst.start()
