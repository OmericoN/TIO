import os
import tempfile

# Never voice audio with a real ElevenLabs key during tests: panel sessions voice holding lines in the background,
# and load_dotenv won't override a variable that is already set.
os.environ["ELEVENLABS_API_KEY"] = ""
# Keep holding lines voiced by mocked tests out of the real cache.
os.environ["PANEL_HOLDING_CACHE"] = tempfile.mkdtemp(prefix="tio-holding-")
os.environ["PANEL_VOICE_PROFILES"] = os.path.join(tempfile.mkdtemp(prefix="tio-voices-"), "profiles.json")
