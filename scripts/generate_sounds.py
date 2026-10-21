"""Generate LogOpen's original, quiet PCM sound cues without external assets."""

import math
from pathlib import Path
import struct
import wave

RATE = 44100


def write_cue(path, notes):
    samples = []
    for frequency, duration in notes:
        count = int(RATE * duration)
        for index in range(count):
            elapsed = index / RATE
            attack = min(1.0, elapsed / 0.008)
            release = min(1.0, (count - index - 1) / (RATE * 0.025))
            envelope = attack * release * math.exp(-3 * elapsed / duration)
            tone = math.sin(2 * math.pi * frequency * elapsed)
            samples.append(round(32767 * 0.10 * envelope * tone))
        samples.extend([0] * int(RATE * 0.018))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def main():
    directory = Path(__file__).resolve().parents[1] / "assets" / "sounds"
    directory.mkdir(parents=True, exist_ok=True)
    for name, notes in {
        "start": [(440.0, 0.065), (554.37, 0.075)],
        "success": [(523.25, 0.075), (659.25, 0.075), (783.99, 0.13)],
        "attention": [(440.0, 0.10), (349.23, 0.14)],
    }.items():
        write_cue(directory / f"{name}.wav", notes)
        print(f"Created {name}.wav")


if __name__ == "__main__":
    main()
