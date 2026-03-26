# ☎️ Rotary Robot

**I made this for my 3 year old daughter. She can pick up her rotary phone handset, ask for a Bluey episode, or to talk to grandma. Robot makes it happen!**

Rotary Robot breathes absurd, futuristic life into standard SIP/analog telephones. It transforms any connected phone into a multi-room, AI-powered smart home hub. Pick up the handset, wait for the AI to greet you, and simply ask it to play a movie, check the weather, or transfer your call to Grandma. 

No coding required. Everything is managed through a beautiful, live Web GUI.

---

## ✨ Features

* **🎛️ The Web Dashboard:** A sleek, fully dynamic web interface to manage API keys, monitor live system logs, and view call history—all without ever touching a configuration file.
* **👨‍👩‍👧‍👦 Multi-Tenant Caller Profiles:** Rotary Robot knows which room is calling! Map specific Caller IDs (extensions) to custom greetings, unique personalities, and specific Target TVs. 
* **🍿 Direct Plex Injection:** Tell the robot what you want to watch. It searches your local Plex server, grabs the deep link, and uses Home Assistant to instantly fling the movie to the TV in the room you are calling from.
* **📞 Voice Dialing (Analog Hacking):** Say "Call Dad," and the robot mathematically synthesizes raw DTMF audio tones (`##Ext`) to securely blind-transfer your call through your PBX. 
* **🧠 Conversational AI Brain:** Powered by OpenAI, the robot acts as the ultimate retro-futuristic operator, seamlessly routing your requests with fluid, natural conversation.

---

## 🛠️ Prerequisites

Before you spin up the container, you will need a few things running on your local network:
1. A SIP PBX (FreePBX, Asterisk, etc.) to handle the phone calls.
2. Home Assistant (with the Android TV integration enabled).
3. Plex Media Server.
4. An OpenAI API Key.
5. An OpenWeatherMap API Key (Optional, but fun).
6. You'll also need an ATA adapter, if you are using a rotary phone I recommend a Grandstream HT802 or HT812

---

## 🚀 Quick Start / GUI Setup

1. Clone this repository and spin up the Docker container (see `docker-compose.yml`).
2. Open your web browser and navigate to the Rotary Robot Dashboard (e.g., `http://<your-docker-ip>:5000`).
3. Enter your OpenAI, OpenWeatherMap, Plex, and Home Assistant credentials into the **Credentials & Interfacing** box.
4. Click **Save Configuration**.
5. **Restart the Docker Container** (SIP credential changes require a hard reboot to register with your PBX).

---

## 📖 Step-by-Step Integrations

### 1. FreePBX Setup (The Secret Sauce)
For Rotary Robot to answer calls and successfully transfer them using Voice Dialing, your PBX needs to be configured correctly.

1. Log into your FreePBX Administration GUI.
2. Go to **Applications -> Extensions** and create a new `pjsip` extension for the Robot (e.g., `Ext 300`).
3. Set a secure password.
4. **CRITICAL STEP:** Go to the **Advanced** tab for this extension. Find **DTMF Signaling** (or DTMF Mode) and change it from `RFC2833` to **`Inband`** (or `Auto`). *If you skip this, FreePBX will be completely deaf to the Robot's voice-dialing transfer beeps!*
5. Submit and click **Apply Config**. 

### 2. Home Assistant & Target TVs
The robot uses Android Debug Bridge (ADB) via Home Assistant to instantly wake up your TV and force-launch Plex deep links. 

1. In Home Assistant, go to **Settings -> Devices & Services**.
2. Add the **Android TV / Fire TV** integration.
3. Enter the IP address of your Fire TV or Nvidia Shield (Make sure ADB Debugging is enabled in the TV's developer settings!).
4. Once added, find the TV in your Home Assistant entities list and copy its exact Entity ID (e.g., `media_player.living_room_shield`).
5. Paste this Entity ID into the **Target TV Entity ID** box on the Rotary Robot Web Dashboard.

### 3. Multi-Room Profiles & Voice Dialing
Have a phone in the kitchen and a phone in the kid's room? You can set up custom profiles so the robot behaves differently depending on who picks up.

1. On the Web Dashboard, click **+ Add Caller Profile**.
2. Enter the **Extension** of the phone (e.g., `101`).
3. Assign it a specific **Target TV Entity ID** (e.g., `media_player.kids_room_tv`). When this phone asks for a movie, it bypasses the global settings and sends it straight to this screen.
4. **Build the Address Book:** In the address book text box, map out names to extensions for the Voice Dialing feature. 
    * *Format:* `Name: Extension` (e.g., `Grandma: 201`, `Dad: 15551234567`).
5. Check **Allow Advanced Tools** and hit **Save**!

### 4. Connecting the Brain (OpenAI)
To make the system fast and conversational, we use OpenAI's API. 

1. Log into `platform.openai.com` and generate an API Key.
2. Paste it into the Dashboard.
3. Select **GPT-4o Mini** from the dropdown. It is insanely fast, incredibly cheap, and perfectly suited for smart home routing tasks. 

---

## 🗣️ How to use it
* Pick up the phone. 
* Hear the dial tone? Good. Dial the Robot's extension.
* Wait for the greeting. 
* **"Hey, can you put on The Goonies?"** -> *Robot searches Plex, wakes up the TV assigned to your caller ID, and deep-links the movie.*
* **"Actually, can you just call Grandma?"** -> *Robot looks up Grandma in your personal address book, blasts DTMF tones down the line, and blind-transfers you through the PBX.*

---
*Disclaimer: Rotary Robot is not responsible for your family members accidentally launching horror movies on the kitchen TV, or the sheer terror of hearing a 1980s telephone cheerfully emulate dial tones in the middle of the night.*
