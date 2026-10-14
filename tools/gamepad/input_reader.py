"""Linux standard gamepad input; evdev is imported only by the board worker."""
import threading
import time


def normalize(value, low, high, deadzone=.14):
    if high <= low:
        return 0.0
    x = max(-1., min(1., 2. * (value - low) / (high - low) - 1.))
    return 0. if abs(x) <= deadzone else round((abs(x) - deadzone) / (1 - deadzone) * (1 if x > 0 else -1), 4)


def is_gamepad(caps):
    # Require two sticks and face buttons; never read keyboards or microphones.
    return {0, 1, 3, 4}.issubset(set(caps.get(3, []))) and 304 in caps.get(1, [])


class InputReader:
    def __init__(self):
        self.lock = threading.RLock()
        self.inputs = {}
        self.selected = None
        self.state = {'connected': False, 'devices': [], 'error': ''}
        threading.Thread(target=self.run, daemon=True, name='gamepad-input').start()

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def select(self, path):
        with self.lock:
            if path not in self.inputs:
                raise ValueError('Gamepad input is not available.')
            self.selected = path

    def run(self):
        import evdev
        last_scan = 0
        previous_id, previous_buttons, press_counts = None, {}, {}
        while True:
            now = time.monotonic()
            with self.lock:
                if now - last_scan > 1:
                    last_scan = now
                    paths = set(evdev.list_devices())
                    for path in paths - self.inputs.keys():
                        try:
                            dev = evdev.InputDevice(path)
                            if not is_gamepad(dev.capabilities(absinfo=False)):
                                dev.close()
                                continue
                            self.inputs[path] = (dev, str(time.monotonic_ns()))
                        except OSError:
                            continue
                    for path in list(self.inputs):
                        if path not in paths:
                            self.inputs.pop(path)[0].close()
                if self.selected not in self.inputs:
                    self.selected = next(iter(self.inputs), None)
                data = {'connected': False, 'devices': [], 'error': '', 'stamp': now}
                if self.selected:
                    dev, identity = self.inputs[self.selected]
                    try:
                        # State ioctls resync even after SYN_DROPPED and detect unplug.
                        try:
                            events = list(dev.read())
                        except BlockingIOError:
                            events = []
                        axes = {}
                        for name, code in [('lx', 0), ('ly', 1), ('rx', 3), ('ry', 4)]:
                            a = dev.absinfo(code)
                            axes[name] = normalize(a.value, a.min, a.max)
                        keys = set(dev.active_keys())
                        buttons = {name: code in keys for name, code in
                                   [('south', 304), ('east', 305), ('north', 307), ('west', 308),
                                    ('lb', 310), ('rb', 311), ('back', 314), ('start', 315),
                                    ('guide', 316), ('ls', 317), ('rs', 318)]}
                        if identity != previous_id:
                            previous_id, previous_buttons, press_counts = identity, {}, {}
                        codes = dict(zip((304,305,307,308,310,311,314,315,316,317,318), buttons))
                        event_buttons = set()
                        for event in events:
                            if event.type == 1 and event.value == 1 and event.code in codes:
                                name = codes[event.code]
                                press_counts[name] = press_counts.get(name, 0) + 1
                                event_buttons.add(name)
                        for name, down in buttons.items():
                            if down and not previous_buttons.get(name) and name not in event_buttons:
                                press_counts[name] = press_counts.get(name, 0) + 1
                        previous_buttons = buttons
                        data.update(button_presses=dict(press_counts))
                        data.update(connected=True, id=identity, path=dev.path, name=dev.name,
                                    axes=axes, buttons=buttons)
                    except OSError:
                        self.inputs.pop(self.selected)[0].close()
                        self.selected = None
                data['devices'] = [{'path': p, 'name': d.name, 'selected': p == self.selected}
                                   for p, (d, _) in self.inputs.items()]
                self.state = data
            time.sleep(.025)
