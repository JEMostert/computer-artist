# The reel

The 15-second reel at the top of the [README](../../README.md) is made by the code in
this folder, without a video framework: a canvas program draws every frame,
headless Chrome rasterizes it, the soundtrack is synthesized in Python, and ffmpeg
encodes the result.

| File | What it does |
| --- | --- |
| `reel.js` | Draws each frame as a pure function of time on a 1920×1080 canvas, at 60 fps. Eight sections, one per bar at 128 BPM. Motion blur averages five sub-frames per frame. |
| `index.html`, `server.py` | The page renders the requested frames and posts each PNG to the server, which writes them to a folder. |
| `render.sh` | Starts the server and a headless Chrome, and waits until every frame is written. |
| `soundtrack.py` | Synthesizes the soundtrack with numpy and scipy, using no samples, and writes `reel.wav` and `cues.json`. It is seeded, so it writes the same file every time. |
| `encode.sh` | Encodes the frames and the audio to H.264 and AAC. |
| `fetch-fonts.sh` | Downloads Archivo, Instrument Serif and JetBrains Mono (SIL OFL) and makes the cuts the reel uses. |

```bash
pip install fonttools numpy scipy      # matplotlib is optional, for spectrogram.png
./fetch-fonts.sh
CHROME=/path/to/chrome-headless-shell ./render.sh frames
python soundtrack.py
./encode.sh frames reel.wav ../assets/computer-artist-reel.mp4
```

`render.sh frames "frames=150,860&sub=1"` renders single frames without motion blur,
for checking a layout. The paintings come from [`../assets/gallery`](../assets/gallery);
see the [gallery](../GALLERY.md) for how they were made.
