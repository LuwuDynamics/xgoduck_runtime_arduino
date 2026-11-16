"""Keeps MQTT/MCP alive when no USB sound devices are installed."""
from types import SimpleNamespace


class NoAudio:
    def __init__(self, **kwargs):
        self.aec = SimpleNamespace(available=False, backend='none')
        self.aec_enabled = False

    def start(self):
        pass

    def close(self):
        pass

    def open_input(self):
        pass

    def close_input(self):
        pass

    def set_capture_enabled(self, enabled):
        pass

    def clear_play_queue(self, **kwargs):
        pass

    def write_opus(self, payload):
        pass

    def play_wav(self, path):
        pass

    def stop_plc(self):
        pass

    def update_server_params(self, **kwargs):
        pass

    def is_playback_idle(self):
        return True

    def wait_playback_done(self, timeout):
        return True
