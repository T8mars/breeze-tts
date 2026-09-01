# Release checklist

- [x] `roadmap.md` scope and version are current.
- [x] Official source revision and model revision are pinned.
- [x] Model manifest passes full size and SHA-256 verification.
- [x] Model files are not embedded in the public portable zip.
- [x] License acceptance is required before model download or use.
- [x] Portable CPython imports all locked dependencies and `pip check` passes.
- [x] Desktop starts on loopback only; CSP, navigation, and IPC allowlists remain enabled.
- [x] Desktop design, clone, direction, cancel, unload, repair, and diagnostics paths pass.
- [x] ComfyUI reports exactly eight T8 nodes through `/object_info`.
- [x] ComfyUI design, clone, and direction workflows produce valid 24 kHz audio.
- [x] Transformers 4.57.3 and a current 5.x environment pass compatibility checks.
- [x] Portable ZIP and the large-package 7-Zip self-extracting EXE are generated and integrity-tested.
- [x] `release-manifest.json`, its independently publishable SHA-256, and `SHA256SUMS.txt` pass offline verification.
- [x] Signing policy is explicit: this unsigned build records `NotSigned`; signed releases are gated by `-RequireSignedWindows`.
- [x] Full `npm audit` reports zero production or build-tool vulnerabilities.
- [x] ComfyUI node ZIP and checksum are generated.
- [x] `NOTICE`, `THIRD_PARTY_NOTICES.md`, `MODEL_LICENSE`, and Apache `LICENSE` are present.
