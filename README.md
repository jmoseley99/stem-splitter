# Stem Splitter

A small local app for jamming along to songs. It splits a track into its separate
parts (vocals, drums, bass, guitar, piano, and everything else), lets you turn any
part up, down, or off, and plays the result back. Handy for muting an instrument so
you can play it yourself, or dropping vocals for a backing track.

Load a song by uploading an MP3 or pasting a YouTube link. There's also an optional
count-in and a metronome to help you keep time.

## Running it

Double-click **`StemSplitter.bat`**. The first time, it sets everything up (a few
minutes); after that it just opens the app in your browser.

Needs Python installed first, from [python.org/downloads](https://www.python.org/downloads/)
(tick "Add python.exe to PATH" during install).

## Built with

[Demucs](https://github.com/facebookresearch/demucs) for separation and
[Streamlit](https://streamlit.io/) for the interface.
