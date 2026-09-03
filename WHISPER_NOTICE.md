# Bundled Whisper transcription model

The Windows Voice Studio package includes `faster-whisper` and a converted Whisper Small checkpoint for offline default transcription.

- Engine: `faster-whisper` 1.2.1
- Model: `Systran/faster-whisper-small`
- Pinned revision: `536b0662742c02347bc0e980a01041f333bce120`
- Model license metadata: MIT
- Source: <https://huggingface.co/Systran/faster-whisper-small>

Only the default `small` checkpoint is bundled. Choosing `tiny`, `base`, `medium`, or `large-v3` downloads that checkpoint on first use into the application's user-data model cache.
