# coding=utf-8

### module that pushes alerts to OctoPrint javascript to guarantee users can always see them
#   all methods should be thread-safe

from collections import deque

ring_buffer = deque(maxlen=5)

# We dont' want to bombard the server with repeated events. So we keep track of the events sent since last restart.
# However, there are probably situations in the future repeated events do need to be propagated to the server.
printer_events_posted = deque(maxlen=20)

def add_alert(alert, plugin, post_to_server=False, attach_snapshot=False):
    global ring_buffer
    if alert in ring_buffer:
        return
    ring_buffer.append(alert)
    plugin._plugin_manager.send_plugin_message(plugin._identifier, {'plugin_updated': True})

    alert_title = alert.get('title')
    if post_to_server and alert_title and not alert_title in printer_events_posted:
        printer_events_posted.append(alert_title)
        plugin.post_printer_event_to_server(
            'Plugin: ' + alert_title,
            '<p><i>OctoPrint plugin error:</i></p><div>' + alert['text'] + '</div>',
            event_class=('WARNING' if alert['level'] == 'warning' else 'ERROR'),
            attach_snapshot=attach_snapshot,
            info_url=alert.get('info_url', None)
        )


def fetch_and_clear():
    global ring_buffer
    msgs = list(ring_buffer)
    ring_buffer.clear()
    return msgs

