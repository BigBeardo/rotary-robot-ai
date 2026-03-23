Markdown
# ☎️🤖 Rotary Robot AI

**A lightning-fast, Dockerized bridge connecting vintage pulse-dial telephones to modern AI (GPT-4o) using SIP, FreePBX, and local machine learning.**

Have you ever wanted to pick up a heavy, mechanical 1970s rotary phone, dial an extension, and have a natural, low-latency conversation with an AI? That is exactly what this project does. 

Rotary Robot AI acts as a SIP client on your local network. When you dial its extension, it answers the phone, records your voice, locally transcribes it using an offline neural engine, streams a response from OpenAI, and speaks it back to you over the telephone line in real-time.

## ✨ Key Features
* **Zero-Latency Streaming:** Uses a custom "staccato" prompt and punctuation triggers to stream text-to-speech audio *while* the AI is still thinking.
* **100% Local STT (Ears):** Uses the highly optimized `faster-whisper` engine running entirely on your local CPU for total privacy and sub-second transcription. No cloud audio uploading required.
* **Vintage Hardware Support:** Fully compatible with mechanical pulse-dialing when paired with the right ATA hardware.
* **Zero-Touch Deployment:** Fully containerized with a secure, web-based initialization screen. No need to manually edit JSON configuration files. 
* **Secure Web Dashboard:** A responsive, Flask-based UI to manage AI personality, adjust voice pitch/speed, view live terminal logs, and review persistent call history.

## 🛠️ Hardware & Network Requirements
1. **A Vintage Telephone:** Any standard analog phone (rotary or touch-tone).
2. **An Analog Telephone Adapter (ATA):** * *CRITICAL NOTE for Rotary Phones:* Most modern ATAs do not understand mechanical pulse dialing. You **must** use an ATA that supports it, such as the **Grandstream HT802** or **HT812**, which have a specific "Pulse Dialing Standard" setting in their firmware. (Alternatively, you can use a pulse-to-tone converter like a Dialgizmo with any ATA).
3. **A SIP Server:** A local PBX server like **FreePBX** or Asterisk to route the extension to the Docker container.
4. **A Linux Host:** Any x86_64 Linux machine to run the Docker container. 

## 🚀 Quick Start Guide

### 1. Clone the Repository
```bash
git clone [https://github.com/YourUsername/rotary-robot-ai.git](https://github.com/YourUsername/rotary-robot-ai.git)
cd rotary-robot-ai
2. Build and Launch the Container
Bash
docker compose up -d --build
Note: The initial build will take a few minutes as it downloads the PyTorch and Whisper machine learning libraries. Docker will automatically generate a secure data/ folder on your host to store your persistent configurations and call logs.

3. System Initialization
Once the container is running, open your web browser and navigate to:
http://<YOUR_SERVER_IP>:5000

You will be greeted by the System Initialization screen. Create a secure local Admin username and password.

Log in and navigate to the Credentials & Interfacing panel.

Input your OpenAI API Key, your OpenWeatherMap API Key, your local FreePBX SIP credentials, and your container's IP address.

Click Securely Save Credentials.

Restart the container (docker restart rotaryrobot) to apply the new SIP network settings.

🧠 How it Works (The Pipeline)
The Call: You pick up the handset and dial the robot's extension. The Grandstream ATA converts the analog voltage into a digital SIP request and sends it to FreePBX. FreePBX routes the call to the Python pyVoIP client running inside Docker.

VAD (Voice Activity Detection): A dynamic, multi-threaded background loop listens to the RTP audio stream, waiting for a brief moment of silence to know you have finished speaking.

Local Transcription: The audio is instantly processed locally by faster-whisper, converting your speech to text in less than a second.

The Brain: The text is sent to the OpenAI API (GPT-4o or GPT-4o-mini).

The Mouth: As the AI generates its response, the Python script intercepts it at every comma or period, feeding those short chunks into the Festival TTS engine. This eliminates the "Wait to Speak" problem, delivering audio to your ear almost instantly.

📝 License
This project is open-source and available for any homelab tinkerer to modify, break, and rebuild.
