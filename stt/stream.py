import pyaudio
import asyncio
import sys
import websockets
import time
import os
import json

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 8000

audio_queue = asyncio.Queue()

def callback(input_data, frame_count, time_info, status_flag):
    audio_queue.put_nowait(input_data)
    return (input_data, pyaudio.paContinue)

async def run():
    clock_cursor = 0.
    audio_cursor = 0.
    transcript_cursor = 0.

    additional_headers = {
        'Authorization': 'token ' + os.environ.get('DEEPGRAM_API_KEY')
    }
    async with websockets.connect(f'wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate={RATE}', additional_headers = additional_headers) as ws:
        async def microphone():
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format = FORMAT,
                channels = CHANNELS,
                rate = RATE,
                input = True,
                frames_per_buffer = CHUNK,
                stream_callback = callback
            )

            stream.start_stream()

            while stream.is_active():
                await asyncio.sleep(0.1)

            stream.stop_stream()
            stream.close()

        async def sender(ws):
            nonlocal clock_cursor, audio_cursor, transcript_cursor
            clock_start = time.perf_counter()
            try:
                while True:
                    data = await audio_queue.get()
                    clock_cursor = time.perf_counter() - clock_start
                    audio_cursor += float(len(data)) / float(RATE) / 2.0
                    await ws.send(data)
            except Exception as e:
                print('Error while sending: ', + string(e))
                raise

        async def receiver(ws):
            nonlocal clock_cursor, audio_cursor, transcript_cursor
            async for msg in ws:
                print(msg)
                msg = json.loads(msg)

                transcript_cursor = msg['start'] + msg['duration']
                transcript = msg['channel']['alternatives'][0]['transcript']

                print(f'Transcript = {transcript}; Measuring... Clock cursor = {clock_cursor:.3f}, Audio cursor: {audio_cursor:.3f}, Transcript cursor = {transcript_cursor:.3f}')

        await asyncio.wait([
            asyncio.ensure_future(microphone()),
            asyncio.ensure_future(sender(ws)),
            asyncio.ensure_future(receiver(ws))
        ])

def main():
    loop = asyncio.get_event_loop()
    asyncio.get_event_loop().run_until_complete(run())

if __name__ == '__main__':
    sys.exit(main() or 0)
