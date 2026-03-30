---
name: stream-clipper
description: >
  Clip streams. Triggers on: clip, process, highlight, harvest, VOD,
  re-clip, list VODs, or any streamer name + action word.
version: 3.2.0
user-invocable: true
metadata:
  openclaw:
    emoji: "\U0001F3AC"
    requires:
      bins:
        - ffmpeg
        - ffprobe
        - python3
        - curl
---

# Stream Clipper

When triggered, you MUST call the `exec` tool. Do NOT just reply with text.

## What to run

Pick the right command based on what the user said:

**User names a VOD/streamer** (e.g. "clip lacy", "do the pokimane stream"):
```json
{"tool":"exec","command":"bash /root/scripts/clip-pipeline.sh --style auto --vod lacy 2>&1","yieldMs":5000}
```

**User specifies stream type** (e.g. "clip the irl lacy stream"):
```json
{"tool":"exec","command":"bash /root/scripts/clip-pipeline.sh --style auto --vod lacy --type irl 2>&1","yieldMs":5000}
```

**User says generic** (e.g. "clip my stream", "process the vod"):
```json
{"tool":"exec","command":"bash /root/scripts/clip-pipeline.sh --style auto 2>&1","yieldMs":5000}
```

**User asks what's available** (e.g. "list vods", "what streams"):
```json
{"tool":"exec","command":"bash /root/scripts/clip-pipeline.sh --list 2>&1","yieldMs":5000}
```

## Style flag
Replace `auto` with: funny, hype, emotional, controversial, or variety — based on the user's words.

## After calling exec

The script takes 20-60 minutes. When exec returns with a session:
1. Call the `process` tool with `action: "poll"` and the session name
2. Keep polling until done
3. Report how many clips were made

## CRITICAL RULES
- You MUST call exec — do not just write a text response
- Keep all messages SHORT (1-2 sentences)
- Never ask the user questions — just run it
