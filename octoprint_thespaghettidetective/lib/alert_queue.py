# coding=utf-8

### module that pushes alerts to OctoPrint javascript to guarantee users can always see them
#   all methods should be thread-safe

from collections import deque

ring_buffer = deque(maxlen=5)

def add_alert(alert, plugin):
    global ring_buffer
    if alert in ring_buffer:
        return
    ring_buffer.append(alert)
    plugin._plugin_manager.send_plugin_message(plugin._identifier, {'new_alert': True})


def fetch_and_clear():
    global ring_buffer
    msgs = list(ring_buffer)
    ring_buffer.clear()
    return msgs

