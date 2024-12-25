import pyaudio
import asyncio
import sys
import websockets
import time
import os
import json
from io import BytesIO
import threading
import janus
import queue

_pyaudio = pyaudio.PyAudio()

TIMEOUT = 0.050

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 48000
CHUNK = 8000

audio_queue = asyncio.Queue()

def callback(input_data, frame_count, time_info, status_flag):
    audio_queue.put_nowait(input_data)
    return (input_data, pyaudio.paContinue)

async def run():
    dg_api_key = os.environ.get("DEEPGRAM_API_KEY")
    if dg_api_key is None:
        print("DEEPGRAM_API_KEY environment variable must be present")
        return

    async with websockets.connect(
        "wss://sts.sandbox.deepgram.com/agent",
        additional_headers={"Authorization": f"Token {dg_api_key}"},
    ) as ws:

        async def microphone():
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
                stream_callback=callback,
            )

            stream.start_stream()

            while stream.is_active():
                await asyncio.sleep(0.1)

            stream.stop_stream()
            stream.close()

        async def sender(ws):
            config_message = {
                "type": "SettingsConfiguration",
                "audio": {
                    "input": {
                        "encoding": "linear16",
                        "sample_rate": 48000,
                    },
                    "output": {
                        "encoding": "linear16",
                        "sample_rate": 16000,
                        "container": "none",
                    },
                },
                "agent": {
                    "listen": {"model": "nova-2"},
                    "think": {
                        "provider": {
                            "type": "open_ai",
                        },
                        "model": "gpt-4o-mini",
                        "instructions": "You are a helpful car seller.",
                    },
                    "speak": {"model": "aura-asteria-en"},
                },
            }

            await ws.send(json.dumps(config_message))

            try:
                while True:
                    data = await audio_queue.get()
                    await ws.send(data)
            except Exception as e:
                print("Error in the sender loop: " + str(e))
                raise

        async def receiver(ws):
            try:
                speaker = Speaker()
                with speaker:
                    async for message in ws:
                        if isinstance(message, str):
                            print(message)

                            # handle barge-in
                            decoded = json.loads(message)
                            if decoded['type'] == 'UserStartedSpeaking':
                                speaker.stop()

                        elif isinstance(message, bytes):
                            await speaker.play(message)
            except Exception as e:
                print(e)

        await asyncio.wait(
            [
                asyncio.ensure_future(microphone()),
                asyncio.ensure_future(sender(ws)),
                asyncio.ensure_future(receiver(ws)),
            ]
        )

def main():
    loop = asyncio.get_event_loop()
    asyncio.get_event_loop().run_until_complete(run())

def _play(audio_out, stream, stop):
    while not stop.is_set():
        try:
            data = audio_out.sync_q.get(True, TIMEOUT)
            stream.write(data)
        except queue.Empty:
            pass

class Speaker:
    def __init__(self):
        self._queue = None
        self._stream = None
        self._thread = None
        self._stop = None

    def __enter__(self):
        self._stream = _pyaudio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=False,
            output=True,
        )
        self._queue = janus.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=_play, args=(self._queue, self._stream, self._stop), daemon=True
        )
        self._thread.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self._stop.set()
        self._thread.join()
        self._stream.close()
        self._stream = None
        self._queue = None
        self._thread = None
        self._stop = None

    async def play(self, data):
        return await self._queue.async_q.put(data)

    def stop(self):
        if self._queue and self._queue.async_q:
            while not self._queue.async_q.empty():
                try:
                    self._queue.async_q.get_nowait()
                except janus.QueueEmpty:
                    break

if __name__ == "__main__":
    sys.exit(main() or 0)
