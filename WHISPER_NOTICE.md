# Bundled Whisper transcription model

The Windows Voice Studio package includes `faster-whisper` and a converted Whisper Large-v3 checkpoint for offline transcription drafts.

> **Required before cloning:** play the reference recording and correct the draft word by word. Whisper does not clean the waveform, and its draft is never treated as ground truth. A mismatched transcript/reference pair may produce repetitions, dragged speech, echo-like artifacts, or an unstable voice; the desktop UI and local API reject an unverified raw reference transcript.

- Engine: `faster-whisper` 1.2.1
- Model: `Systran/faster-whisper-large-v3`
- Pinned revision: `edaa852ec7e145841d8ffdb056a99866b5f0a478`
- Model license metadata: MIT
- Source: <https://huggingface.co/Systran/faster-whisper-large-v3>

Only `large-v3` is exposed by this release. Its output is always treated as an unverified draft and never silently replaces the exact reference transcript required by voice cloning.
