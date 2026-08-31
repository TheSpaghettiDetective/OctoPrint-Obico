JANUS_DIAGNOSTIC_SCALARS = {
    'active', 'bytes', 'bytes_lastsec', 'connected', 'disabled', 'direction',
    'dtls-state', 'fir', 'firs', 'ice-mode', 'ice-role', 'id', 'jitter',
    'lost', 'mid', 'mindex', 'nack', 'nacks', 'nominated', 'packets', 'pli',
    'plis', 'queued-packets', 'ready', 'retransmissions', 'rtt', 'state', 'type',
    'audio_bytes', 'audio_bytes_lastsec', 'audio_nacks', 'audio_packets',
    'audio_retransmissions', 'data_bytes', 'data_packets', 'do_audio_nacks',
    'do_video_nacks', 'in-link-quality', 'in-media-link-quality', 'jitter-local',
    'jitter-remote', 'lost-by-remote', 'out-link-quality', 'out-media-link-quality',
    'video_bytes', 'video_bytes_lastsec', 'video_nacks', 'video_packets',
    'video_retransmissions',
}
JANUS_DIAGNOSTIC_CONTAINERS = {
    'audio', 'components', 'dtls', 'in', 'in_stats', 'info', 'main', 'media',
    'out', 'out_stats', 'rtcp', 'rtcp_stats', 'sim1', 'sim2', 'stats', 'streams',
    'video', 'video-sim1', 'video-sim2', 'video-simulcast-1',
    'video-simulcast-2', 'webrtc',
}


def interval_bitrate_kbps(total_size, previous_total_size, elapsed_seconds):
    """Return the actual wall-clock output rate between two FFmpeg samples."""
    try:
        total_size = int(total_size)
        previous_total_size = int(previous_total_size)
        elapsed_seconds = float(elapsed_seconds)
    except (TypeError, ValueError):
        return None

    if total_size < previous_total_size or elapsed_seconds <= 0:
        return None

    return (total_size - previous_total_size) * 8.0 / elapsed_seconds / 1000.0


def extract_janus_diagnostic_stats(value):
    """Keep media counters and connection state while excluding SDP and addresses."""
    if isinstance(value, list):
        return [item for item in (extract_janus_diagnostic_stats(item) for item in value) if item]
    if not isinstance(value, dict):
        return None

    result = {}
    for key, child in value.items():
        if key in JANUS_DIAGNOSTIC_SCALARS and not isinstance(child, (dict, list)):
            result[key] = child
        elif key in JANUS_DIAGNOSTIC_CONTAINERS:
            extracted = extract_janus_diagnostic_stats(child)
            if extracted:
                result[key] = extracted
    if isinstance(result.get('bytes_lastsec'), (int, float)):
        result['bitrate_kbps'] = result['bytes_lastsec'] * 8.0 / 1000.0
    for media_type in ('audio', 'video'):
        bytes_key = '{}_bytes_lastsec'.format(media_type)
        if isinstance(result.get(bytes_key), (int, float)):
            result['{}_bitrate_kbps'.format(media_type)] = result[bytes_key] * 8.0 / 1000.0
    return result
