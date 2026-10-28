# MicroDuck vocal synthesizer attribution

This package is adapted from Pollen Robotics MicroDuck, distributed under the
Apache License, Version 2.0 (included as LICENSE).

Upstream: https://github.com/pollen-robotics/microduck
Pinned revision: d7560ef059c73fab4177eb247c1774013f6feb9d
Source files: sounds/src/personality.rs, sounds/src/synth.rs, sounds/src/voices.rs,
and the seed derivation in sounds/src/lib.rs.

The upstream authors credit apirrone/microduck_sounds as the original Python
recipe design. This adaptation translates the licensed Rust source; it does
not redistribute that separate Python repository.

Modifications for XGO Duck: NumPy DSP and PCG64 RNG (so rendered samples differ
from upstream), one bounded joy call instead of held-trigger loops, edge fades,
band-limited PCM resampling, gain headroom, and playback through the existing
chat codec. No upstream robot daemon, hardware driver, or parrot experiment is
included. Same device identity and variant reproduce the same local voice.
