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

    alert_title = alert.get('title')
    if post_to_server and alert_title:
        event_data = dict(
            event_title = 'Obico Plugin: ' + alert_title,
            event_text = '<p><i>OctoPrint plugin error:</i></p><div>' + alert['text'] + '</div>',
            event_class = ('WARNING' if alert['level'] == 'warning' else 'ERROR'),
            event_type = 'PRINTER_ERROR',
            info_url = alert.get('info_url', None),
        )
        plugin.passthru_printer_event_to_client(event_data)
        plugin.post_printer_event_to_server(event_data, attach_snapshot=attach_snapshot, spam_tolerance_seconds=60*60*24)


def fetch_and_clear():
    global ring_buffer
    msgs = list(ring_buffer)
    ring_buffer.clear()
    return msgs

