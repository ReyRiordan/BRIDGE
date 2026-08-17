"""
BRIDGE control plane — the thin FastAPI app that runs as a Lambda (Mangum).

Serves scenario config to the SPA and mounts `voice_kit`'s signaling router
(`/voice/{session_id}/start|signal|end`) so the browser never talks to the
AgentCore data plane directly. Deliberately pipecat-free.

Implemented in [Rewrite C]; this package is the scaffold.
"""
