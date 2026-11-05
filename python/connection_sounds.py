"""Play each connection or setup cue once without blocking runtime heartbeats."""
import asyncio
import contextlib
import time

KINDS = {'network': 'greet', 'binding_required': 'speech', 'voice_service': 'wheee'}
SOFTWARE_GAIN = 3.


class ConnectionSounds:
    def __init__(self, play, clock=time.monotonic):
        self.play, self.clock = play, clock
        self.completed = set()
        self.task = None
        self.current = None
        self.retry_at = 0.
        self.last = {'state': 'waiting'}

    @property
    def offline_busy(self):
        return self.current in ('network', 'binding_required') and self.task is not None and not self.task.done()

    def poll(self, network_connected, voice_connected, *, binding_required=False):
        if self.task is not None and not self.task.done(): return
        if self.clock() < self.retry_at: return
        # A confirmed voice connection also proves usable networking when host
        # provisioning status is temporarily unavailable. Cached binding cannot.
        ready = {'network': network_connected is True or voice_connected is True,
                 'binding_required': (network_connected is True and binding_required
                                      and not voice_connected and 'network' in self.completed),
                 'voice_service': voice_connected is True}
        event = next((name for name in KINDS if ready[name] and name not in self.completed), None)
        if event is None: return
        self.current = event
        self.task = asyncio.create_task(self._play(event))

    async def _play(self, event):
        gain = 1. if event == 'binding_required' else SOFTWARE_GAIN
        self.last = dict(state='preparing', event=event, kind=KINDS[event], software_gain=gain)
        try:
            if await self.play(event, KINDS[event], gain):
                self.completed.add(event)
                self.last = dict(self.last, state='completed')
                self.retry_at = 0.
                print('[connection-sound]', event, f'completed; gain={gain:g}', flush=True)
            else:
                self.last = dict(self.last, state='waiting_audio')
                self.retry_at = self.clock() + 5.
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last = dict(self.last, state='retrying', error=type(exc).__name__)
            self.retry_at = self.clock() + 5.

    def status(self):
        return dict(self.last, completed=[name for name in KINDS if name in self.completed])

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await self.task
