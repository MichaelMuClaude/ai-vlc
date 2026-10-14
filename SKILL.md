---
name: ai-vlc
description: Control VLC playback of music or video in the background, with play, add, pause, stop, next, previous, volume, and status. Use when the user asks to play a song or film, pause or skip, or change the volume.
---

# ai-vlc

## Commands

```bash
ai-vlc play <path-or-url>       # replace the playlist and play (file, folder, or stream URL)
ai-vlc add <path-or-url>        # add to the playlist and play it
ai-vlc pause                    # toggle play and pause
ai-vlc stop                     # stop playback and release VLC
ai-vlc next
ai-vlc prev
ai-vlc volume <0-100>           # VLC's own volume
ai-vlc system-volume [0-100]    # system output volume; without a number, shows it
ai-vlc status                   # what is playing, its state, and volume
```

## Reading the output

- Every command prints a short line on success. On failure it prints `error: ...` to stderr and exits with a non-zero code.
- `command sent but its effect was not confirmed` means VLC did not confirm the change in time. Run `status` before trying again.
- `nothing is playing` means there is no active player. Start one with `play`.

## Choosing the volume

- `volume` changes VLC's own volume and does not change the system volume.
- `system-volume` changes the output device. If a track plays but cannot be heard, check the system volume too.

## Limits

- One player at a time.
- Needs an X11 session for video, and VLC installed.
