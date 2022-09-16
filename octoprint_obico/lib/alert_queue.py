# coding=utf-8

### module that pushes alerts to OctoPrint javascript to guarantee users can always see them
#   all methods should be thread-safe

from collections import deque

ring_buffer = deque(maxlen=5)

def add_alert(alert, plugin, post_to_server=False, attach_snapshot=False):
    global ring_buffer
    if alert in ring_buffer:
        return
    ring_buffer.append(alert)
    plugin._plugin_manager.send_plugin_message(plugin._identifier, {'plugin_updated': True})

    if post_to_server:
        self.post_printer_event_to_server(
            alert['title'],
            alert['text'],
            event_class=('WARNING' if alert['level'] == 'warning' else 'ERROR'),
            attach_snapshot=attach_snapshot,
            info_url=alert.get('info_url', None)
        )


def fetch_and_clear():
    global ring_buffer
    msgs = list(ring_buffer)
    ring_buffer.clear()
    return msgs

