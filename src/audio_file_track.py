from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame
import numpy as np
import asyncio

class AudioFileTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, samples: np.ndarray, sample_rate: int = 48000):
        super().__init__()
        self.samples = samples
        self.sample_rate = sample_rate
        self.samples_per_frame = 960  # 20ms at 48kHz
        self.position = 0
        self._start_time = None

    async def recv(self) -> AudioFrame:
        if self._start_time is None:
            self._start_time = asyncio.get_event_loop().time()

        # Calculate timing
        elapsed = asyncio.get_event_loop().time() - self._start_time
        target_samples = int(elapsed * self.sample_rate)

        # Wait for proper timing
        samples_ahead = self.position - target_samples
        if samples_ahead > self.samples_per_frame:
            await asyncio.sleep(samples_ahead / self.sample_rate)

        # Get audio chunk
        end_pos = min(self.position + self.samples_per_frame, len(self.samples))
        if self.position >= len(self.samples):
            # Loop or send silence after audio ends
            chunk = np.zeros(self.samples_per_frame, dtype=np.int16)
        else:
            chunk = self.samples[self.position:end_pos]
            if len(chunk) < self.samples_per_frame:
                chunk = np.pad(chunk, (0, self.samples_per_frame - len(chunk)))

        self.position = end_pos

        # Create AudioFrame
        frame = AudioFrame(format="s16", layout="mono", samples=self.samples_per_frame)
        frame.sample_rate = self.sample_rate
        frame.pts = self.position
        frame.planes[0].update(chunk.tobytes())

        return frame