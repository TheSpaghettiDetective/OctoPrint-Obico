import requests
import json
import logging
import threading
import time
import os
try:
    from urllib.parse import urljoin
except ImportError:
    from urlparse import urljoin

from .ws import WebSocketClient

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')


class LocalTunnel(object):

    def __init__(self, base_url, on_http_response, on_ws_message, data_dir):
        self.base_url = base_url
        self.on_http_response = on_http_response
        self.on_ws_message = on_ws_message
        self.ref_to_ws = {}
        self.cj_path = os.path.join(data_dir, '.tunnel.cj.json')
        self.request_session = requests.Session()
        try:
            with open(self.cj_path, 'r') as fp:
                self.request_session.cookies = requests.cookies.cookiejar_from_dict(json.load(fp))
        except:
            pass   # Start with a clean session without cookies if cookie jar loading fails for any reason

    def send_http_to_local(
            self, ref, method, path,
            params=None, data=None, headers=None, timeout=30):

        throwing = False
        url = urljoin(self.base_url, path)

        try:
            resp = getattr(self.request_session, method)(
                url,
                params=params,
                headers={k: v for k, v in headers.items() if k != 'Cookie'},
                data=data,
                timeout=timeout,
                allow_redirects=True)

            save_cookies = False
            if resp.status_code == 304:      # failed to authenticate
                self.request_session.cookies.clear()
                save_cookies = True

            if resp.headers.pop('Set-Cookie', None) or save_cookies: # Stop set-cookie from being propagated to TSD server
                with open(self.cj_path, 'w') as fp:
                    json.dump(self.request_session.cookies.get_dict(), fp)

            resp_data = {
                'status': resp.status_code,
                'content': resp.content,
                'headers': {k: v for k, v in resp.headers.items()},
            }
        except Exception as ex:
            throwing = True
            resp_data = {
                'status': 502,
                'content': repr(ex),
                'headers': {}
            }

        self.on_http_response(
            {'http.tunnel': {'ref': ref, 'response': resp_data}},
            throwing=throwing,
            as_binary=True)
        return

    def send_ws_to_local(self, ref, path, data, type_):
        print(type_)
        ws = self.ref_to_ws.get(ref, None)

        if type_ == 'tunnel_close':
            if ws is not None:
                ws.close()
                print('op ws closed')
            return

        if ws is None:
            self.connect_octoprint_ws(ref, path)
            time.sleep(1)  # Wait to make sure websocket is established before `send` is called

        if data is not None:
            ws.send(data)

    def connect_octoprint_ws(self, ref, path):
        def on_ws_error(ws, ex):
            _logger.error("OctoPrint WS error %s", ex)

        def on_ws_close(ws):
            _logger.info("OctoPrint WS is closing")
            if ref in self.ref_to_ws:
                self.on_ws_message(
                    {'ws.tunnel': {'ref': ref, 'data': None, 'type': 'octoprint_close'}},
                    throwing=False,
                    as_binary=True)
                del self.ref_to_ws[ref]

        def on_ws_msg(ws, data):
            self.on_ws_message(
                {'ws.tunnel': {'ref': ref, 'data': data, 'type': 'octoprint_message'}},
                throwing=False,
                as_binary=True)

        url = urljoin(self.base_url, path)
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
