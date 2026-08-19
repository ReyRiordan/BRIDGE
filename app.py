import os

import gradio as gr
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastrtc import ReplyOnPause, Stream, AlgoOptions, get_twilio_turn_credentials

from backend.agents import ParakeetSTT, OpenRouterChat, InworldTTS
from backend.game import load_scenario, load_patient_prompt
from backend import handlers
from backend.routes import register_routes


load_dotenv('.env')

SCENARIO = load_scenario("resources/scenario_1.json")
PATIENT_PROMPT = load_patient_prompt("resources/patient.txt", "resources/patient.json")
SYSTEM_PROMPT = open("resources/referee.txt", encoding="utf8").read()

STT = ParakeetSTT(os.getenv("TOGETHER_API_KEY"))
SYSTEM_LLM = OpenRouterChat(
    os.getenv("OPENROUTER_API_KEY"),
    model=os.getenv("SYSTEM_AGENT_MODEL", "anthropic/claude-haiku-4.5"),
    effort=os.getenv("SYSTEM_AGENT_EFFORT", "none"),
)
PATIENT_LLM = OpenRouterChat(
    os.getenv("OPENROUTER_API_KEY"),
    model=os.getenv("PATIENT_AGENT_MODEL", "anthropic/claude-haiku-4.5"),
    effort=os.getenv("PATIENT_AGENT_EFFORT", "none"),
)
TTS = InworldTTS(os.getenv("INWORLD_API_KEY"))

# Inject singletons into handlers module
handlers.STT = STT
handlers.SYSTEM_LLM = SYSTEM_LLM
handlers.PATIENT_LLM = PATIENT_LLM
handlers.TTS = TTS
handlers.SCENARIO = SCENARIO
handlers.PATIENT_PROMPT = PATIENT_PROMPT
handlers.SYSTEM_PROMPT = SYSTEM_PROMPT

algo_options = AlgoOptions(
    audio_chunk_duration=1.0,
    started_talking_threshold=0.3,
    speech_threshold=0.3,
)
# WebRTC needs a TURN server when the browser and server are on different
# networks (e.g. deployed). Locally this is unnecessary, so only enable it
# when Twilio credentials are present.
rtc_configuration = None
if os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"):
    rtc_configuration = get_twilio_turn_credentials()

stream = Stream(
    modality="audio",
    mode="send-receive",
    handler=ReplyOnPause(handlers.response, input_sample_rate=16000, algo_options=algo_options),
    concurrency_limit=1,
    rtc_configuration=rtc_configuration,
    ui_args={"title": "MEWAI Patient"},
)

app = FastAPI()
app.mount("/scenes", StaticFiles(directory="scenes"), name="scenes")
app.mount("/visuals", StaticFiles(directory="visuals"), name="visuals")
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")
app = gr.mount_gradio_app(app, stream.ui, path="/gradio")

register_routes(app, SCENARIO)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
