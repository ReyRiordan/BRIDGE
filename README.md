# BRIDGE — Behavioral Response and Interactive De-escalation Guided Education

A voice-to-voice medical training simulation for practicing behavioral de-escalation. Medical students speak with an AI patient in real time, make de-escalation decisions, and watch an agitation meter and scene visuals respond dynamically. Built with Nethra.

## Setup

**Prerequisites:** Python 3.11+

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
OPENROUTER_API_KEY=...   # Claude Haiku via OpenRouter
FIREWORKS_API_KEY=...    # Whisper STT
INWORLD_API_KEY=...      # Inworld TTS
```

## Run

```bash
python3 app.py
```

Visit [http://localhost:7860](http://localhost:7860).

## How It Works

```
Student speaks
    │
    ▼
WhisperSTT (Fireworks)
    │  transcribed text
    ▼
System Agent (Claude Haiku)
    │  detects de-escalation actions → updates escalation bar + scene
    ▼
Patient Agent (Claude Haiku)
    │  generates in-character response based on escalation level
    ▼
InworldTTS (Inworld)
    │  streams audio back to student
    ▼
WebSocket → frontend updates (scene, bar, transcript, timer)
```

**Win:** reduce escalation to 0 before the 5-minute timer runs out.  
**Lose:** escalation reaches 10, or time runs out.

## Project Structure

```
app.py              Entry point
backend/
  agents.py         AI wrappers (STT, LLM, TTS)
  game.py           Game state + scenario loading
  handlers.py       Per-turn simulation loop
  routes.py         FastAPI endpoints + WebSocket
frontend/
  index.html        UI (4 screens: start, intro, game, end)
  style.css         Styles
  app.js            Frontend logic
resources/
  scenario_1.json   Scenario config
  patient.json      Patient case file
  patient.txt       Patient agent prompt
  referee.txt       Referee agent prompt
scenes/             Scene images (JPG)
```

See `CLAUDE.md` for full architecture details.
