# Security

Unbubble handles people's most private conversations, so security reports get priority.

**Report a vulnerability privately** through GitHub: go to the repository's **Security** tab and choose **Report a vulnerability**. Please don't open a public issue. We aim to reply within 48 hours and to ship fixes for serious issues within 7 days.

## Threat model in brief

| Boundary | Protection |
| --- | --- |
| Other websites → helper | Loopback-only listener. WebSocket `Origin` must be thegreymirror.com, justlay.me or the helper's own UI. Requests with a non-loopback `Host` are rejected (DNS rebinding). `/control/url` refuses any request carrying an `Origin`. |
| Malicious `unbubble://pair` links | Relay host allowlist (`relay.thegreymirror.com` only). The first pairing from a browser key needs a native **Allow** prompt showing a short code derived from both public keys. |
| Relay operator / network | ECDH P-256 → HKDF-SHA256 → AES-256-GCM per direction. The web public key is delivered out of band (OS URL handler), so the relay cannot substitute it. Counter nonces reject replays and reordering. |
| Data at rest | Archives and per-device sync data live in the helper's app-data folder with owner-only permissions. People can delete exports and forget devices from the interface. |
| Exports | `export.reveal` only opens folders this helper saved in the current session. The export dialog warns when the chosen folder syncs to OneDrive. |
| Apple driver install | Only Apple's own `Apple.AppleMobileDeviceSupport` package, installed through winget on the person's click; Windows shows its admin prompt. |
| Uploads | Nothing leaves the PC unless the person picks one conversation and chooses to upload it to the host site. |
| Standalone UI | Strict Content-Security-Policy (`script-src 'self'`, no third-party origins), `frame-ancestors 'none'`. |

The automated tests in `helper/tests/test_engine.py` and `tests/relay_e2e.mts` cover these boundaries.
