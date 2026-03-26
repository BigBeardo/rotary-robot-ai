import time
import wave
import subprocess
import requests
import json
import threading
import queue
import os
import re
import random
import concurrent.futures
import numpy as np
import speech_recognition as sr
from datetime import datetime
from openai import OpenAI
from faster_whisper import WhisperModel
from pyVoIP.VoIP import VoIPPhone, CallState, InvalidStateError

# Ensure the data directory exists
os.makedirs("data", exist_ok=True)

# --- LOCAL AI ENGINE ---
print("[SYSTEM] Loading local Whisper AI into memory...")
whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")
print("[SYSTEM] Local Whisper AI ready.")

# --- UTILITIES & LOGGING ---
def get_config(key, default=""):
    try:
        with open("data/config.json", "r") as f:
            return json.load(f).get(key, default)
    except Exception:
        return default

def robot_print(msg):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    try:
        with open("data/robot.log", "a") as f:
            f.write(formatted_msg + "\n")
    except Exception:
        pass

def log_call_history(caller_id, duration_seconds):
    file_exists = os.path.isfile("data/call_history.csv")
    timestamp = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    try:
        with open("data/call_history.csv", "a") as f:
            if not file_exists:
                f.write("Date & Time,Caller ID,Duration (Seconds)\n")
            f.write(f"{timestamp},{caller_id},{duration_seconds}\n")
    except Exception as e:
        robot_print(f"[Logger Error] Could not save to CSV: {e}")

def get_current_weather():
    default_zip = get_config("weather_zip", "80202")
    weather_key = get_config("weather_api_key")
    if not weather_key:
        return "Weather API key not configured."

    url = f"http://api.openweathermap.org/data/2.5/weather?zip={default_zip},us&appid={weather_key}&units=imperial"
    robot_print(f"[DEBUG] Pre-fetching local weather...")
    
    try:
        response = requests.get(url, timeout=3)
        data = response.json()
        if response.status_code == 200:
            temp = round(data['main']['temp'])
            desc = data['weather'][0]['description']
            city_name = data.get('name', 'your area')
            return f"{temp} degrees Fahrenheit with {desc} in {city_name}."
        else:
            return "Weather data currently unavailable."
    except Exception as e:
        robot_print(f"[AI Brain] Weather API Pre-fetch Error: {e}")
        return "Weather service offline."

# --- HOME ASSISTANT BRIDGE ---
def call_home_assistant(action, payload):
    ha_url = "http://192.168.50.211:8123"
    ha_token = get_config("ha_token", "")
    
    if not ha_token:
        robot_print("[Home Assistant] Error: HA Token missing in config.json")
        return "Error: Tell the user that the Home Assistant token is missing from the configuration."
        
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    
    try:
        url = f"{ha_url}/api/services/{action}"
        robot_print(f"[Home Assistant] Firing command to {url}...")
        
        # Fire and Forget: 3 second timeout prevents the robot from hanging up
        response = requests.post(url, headers=headers, json=payload, timeout=3)
        
        if response.status_code in [200, 201]:
            robot_print("[Home Assistant] Command successful!")
            return "Success: The command was executed."
        else:
            robot_print(f"[Home Assistant] Failed with status {response.status_code}: {response.text}")
            return f"Error: Home Assistant returned status code {response.status_code}."
            
    except requests.exceptions.ReadTimeout:
        robot_print("[Home Assistant] Command delivered, but HA is taking a while to respond (normal for TV/Plex).")
        return "Success: The command was sent to the TV, but it might take a few seconds to buffer and start playing."
    except Exception as e:
        robot_print(f"[Home Assistant] Connection failed: {e}")
        return f"Error connecting to Home Assistant: {e}"

# --- AUDIO PIPELINE & DTMF SYNTHESIS ---
def is_silent(chunk, threshold=1.0): 
    if not chunk: return True
    deviation = sum(abs(b - 128) for b in chunk) / len(chunk)
    
    if deviation >= threshold:
        bar_length = min(int(deviation), 40)
        robot_print(f"[MIC LEVEL] {deviation:5.1f} |{'█' * bar_length}")
        
    return deviation < threshold

def execute_dtmf_transfer(call, extension):
    robot_print(f"[SIP] Building DTMF audio track for {extension}...")
    dial_string = f"##{extension}"
    
    # Using pure Python math to avoid any library dependency issues
    import math
    dtmf_freqs = {
        '1': (697, 1209), '2': (697, 1336), '3': (697, 1477),
        '4': (770, 1209), '5': (770, 1336), '6': (770, 1477),
        '7': (852, 1209), '8': (852, 1336), '9': (852, 1477),
        '*': (941, 1209), '0': (941, 1336), '#': (941, 1477),
    }
    
    sample_rate = 8000
    duration = 0.25 # 250ms tone duration
    
    audio_data = bytearray()
    
    for i, digit in enumerate(dial_string):
        if digit in dtmf_freqs:
            f1, f2 = dtmf_freqs[digit]
            
            # 1. Generate the tone math
            for step in range(int(sample_rate * duration)):
                t = step / sample_rate
                tone = 0.5 * (math.sin(2 * math.pi * f1 * t) + math.sin(2 * math.pi * f2 * t))
                val = int((tone + 1.0) * 127.5) # Convert to 8-bit unsigned
                audio_data.append(val)
            
            # 2. Add the silence gap (800ms after the "##" command, 100ms between digits)
            pause_ms = 800 if (i == 1 and digit == '#') else 100
            audio_data.extend([128] * int(sample_rate * (pause_ms / 1000.0)))

    # 3. Hand the ENTIRE audio block to pyVoIP at once
    try:
        robot_print("[SIP] Streaming DTMF track to PBX...")
        call.write_audio(bytes(audio_data))
        
        # Calculate exactly how long the audio track is, and wait for it to finish
        play_time_seconds = len(audio_data) / 8000.0
        time.sleep(play_time_seconds + 1.0) # Add 1s buffer for FreePBX to process
        robot_print("[SIP] Transmission complete.")
    except Exception as e:
        robot_print(f"[SIP] DTMF Transmission Error: {e}")

def record_audio_dynamic(call, silence_timeout=0.8, max_duration=15):
    robot_print("[Rotary Robot] Listening... (MULTI-THREADED RAM MODE)")
    audio_queue = queue.Queue()
    is_recording = True
    
    def audio_reader():
        while is_recording and call.state == CallState.ANSWERED:
            try:
                chunk = call.read_audio(160)
                if chunk: audio_queue.put(chunk)
            except Exception: break
                
    threading.Thread(target=audio_reader, daemon=True).start()
    
    accumulated_chunks = []
    start_time = time.time()
    last_speech_time = time.time()
    has_spoken = False
    
    try:
        while call.state == CallState.ANSWERED:
            if time.time() - start_time > max_duration:
                robot_print("[Rotary Robot] Maximum 15s recording time reached.")
                break
            if has_spoken:
                if time.time() - last_speech_time > silence_timeout:
                    robot_print("[Rotary Robot] End of speech detected.")
                    break
            else:
                if time.time() - start_time > 7.0: 
                    robot_print("[Rotary Robot] No initial speech detected. Timing out.")
                    break
                    
            try:
                chunk = audio_queue.get(timeout=0.01)
                is_sil = is_silent(chunk)
                
                if not has_spoken:
                    if is_sil:
                        continue 
                    else:
                        has_spoken = True
                        last_speech_time = time.time()
                        accumulated_chunks.append(chunk)
                else:
                    accumulated_chunks.append(chunk)
                    if not is_sil:
                        last_speech_time = time.time()
            except queue.Empty: pass
    finally:
        is_recording = False 
        
    if not has_spoken:
        return None

    raw_bytes = b"".join(accumulated_chunks)
    
    try:
        ffmpeg_cmd = [
            "ffmpeg", "-y", 
            "-f", "u8", "-ar", "8000", "-ac", "1", "-i", "pipe:0",
            "-af", "volume=4.0", 
            "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"
        ]
        process = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out_bytes, _ = process.communicate(input=raw_bytes)
        
        audio_np = np.frombuffer(out_bytes, np.int16).astype(np.float32) / 32768.0
        return audio_np
        
    except Exception as e:
        robot_print(f"[Rotary Robot] RAM Transcoding Error: {e}")
        return None

# --- AI BRAIN ---
def transcribe_audio(audio_data):
    robot_print("[AI Brain] Decoding speech from RAM...")
    try:
        segments, info = whisper_model.transcribe(audio_data, beam_size=5)
        text = "".join([segment.text for segment in segments]).strip()
        if text:
            robot_print(f"🗣️  USER SAID: \"{text}\"")
            return text
        return None
    except Exception as e:
        robot_print(f"[AI Brain] Local Whisper STT Error: {e}")
        return None

def query_gpt4o(messages, call=None):
    openai_key = get_config("openai_api_key")
    if not openai_key: return "OpenAI API Key is missing.", messages
    client = OpenAI(api_key=openai_key)
    
    gpt_model = get_config("gpt_model", "gpt-4o")
    max_tokens = int(get_config("max_tokens", 150))

    try:
        response = client.chat.completions.create(model=gpt_model, messages=messages, max_tokens=max_tokens, temperature=0.7)
        return response.choices[0].message.content, messages
    except Exception as e:
        return "Simulator connection error.", messages

def query_and_stream_response(messages, call, allow_ha=False, caller_id="Unknown"):
    robot_print(f"[AI Brain] Consulting GPT (Fluid Background Mode)...")
    openai_key = get_config("openai_api_key")
    if not openai_key:
        error_msg = "Please configure my API keys in the dashboard."
        generate_and_speak(call, error_msg, wait=True)
        return error_msg, messages

    client = OpenAI(api_key=openai_key)
    gpt_model = get_config("gpt_model", "gpt-4o")
    max_tokens = int(get_config("max_tokens", 150))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "play_plex_media",
                "description": "Plays a specific TV show or movie on the requested TV using Plex.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "media_title": {
                            "type": "string",
                            "description": "The exact title of the show or movie to play (e.g., 'Bluey', 'The Last Starfighter')"
                        },
                        "media_type": {
                            "type": "string",
                            "enum": ["movie", "episode"],
                            "description": "Whether the requested media is a 'movie' or an 'episode'."
                        },
                        "library_name": {
                            "type": "string",
                            "description": "The exact name of the Plex library to search. Guess either 'Movies' or 'TV Shows'."
                        }
                    },
                    "required": ["media_title", "media_type", "library_name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "transfer_call",
                "description": "Transfers the current active phone call to a person listed in the user's personal address book.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact_name": {
                            "type": "string",
                            "description": "The name of the person the user wants to call (e.g., 'Grandma', 'Dad')"
                        }
                    },
                    "required": ["contact_name"]
                }
            }
        }
    ]

    def fetch_gpt():
        start_t1 = time.time()
        
        api_args = {
            "model": gpt_model, 
            "messages": messages, 
            "max_tokens": max_tokens, 
            "temperature": 0.7
        }
        
        if allow_ha:
            api_args["tools"] = tools
            api_args["tool_choice"] = "auto"
            
        response = client.chat.completions.create(**api_args)
        message = response.choices[0].message
        
        if message.tool_calls:
            robot_print(f"[AI Brain] Tool Call Triggered: {message.tool_calls[0].function.name}")
            messages.append(message)
            
            for tool_call in message.tool_calls:
                
                # --- NEW TOOL: TRANSFER CALL (DTMF HACK) ---
                if tool_call.function.name == "transfer_call":
                    args = json.loads(tool_call.function.arguments)
                    contact_name = args.get("contact_name", "").lower()
                    
                    # Fetch address book specifically for THIS caller
                    caller_overrides = get_config("caller_overrides", {})
                    address_book = {}
                    if caller_id in caller_overrides:
                        address_book = caller_overrides[caller_id].get("address_book", {})
                    
                    target_number = None
                    # Simple fuzzy lookup
                    for name, number in address_book.items():
                        if name.lower() in contact_name or contact_name in name.lower():
                            target_number = number
                            break
                            
                    if target_number:
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": tool_call.function.name, "content": f"Success. You are transferring the call to {target_number}."})
                        
                        # Tell the caller we are transferring them before we drop out
                        generate_and_speak(call, f"Transferring your call to {contact_name}. Please hold.", wait=True)
                        
                        # Fire the DTMF tones synchronously so we don't hang up too early!
                        execute_dtmf_transfer(call, target_number)
                        
                        # We MUST return "TRANSFER_TRIGGERED" so the main loop knows to hang up the AI
                        return "TRANSFER_TRIGGERED"
                    else:
                        error_msg = f"Error: '{contact_name}' is not in the address book for extension {caller_id}. Tell the user you don't have their number."
                        robot_print(f"[SIP] {error_msg}")
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": tool_call.function.name, "content": error_msg})

                # --- TOOL: PLAY PLEX MEDIA ---
                elif tool_call.function.name == "play_plex_media":
                    args = json.loads(tool_call.function.arguments)
                    media_title = args.get("media_title")
                    media_type = args.get("media_type", "movie")
                    
                    plex_ip = get_config("plex_ip")
                    plex_token = get_config("plex_token")
                    plex_machine_id = get_config("plex_machine_id")

                    if not plex_ip or not plex_token or not plex_machine_id:
                        robot_print("[Plex Error] Plex credentials missing from config.json")
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": tool_call.function.name, "content": "Error: Plex credentials missing."})
                        continue

                    # --- ROUTING LOGIC: Determine which TV to target ---
                    target_tv = None
                    caller_overrides = get_config("caller_overrides", {})
                    
                    if caller_id in caller_overrides:
                        target_tv = caller_overrides[caller_id].get("target_tv")
                    
                    if not target_tv:
                        # Polite Refusal: The caller has no TV assigned to their profile
                        error_msg = f"No TV is assigned to extension {caller_id}. Tell the user they need to configure a Target TV for this phone in the dashboard."
                        robot_print(f"[Plex Routing Error] {error_msg}")
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": tool_call.function.name, "content": error_msg})
                        continue

                    import urllib.parse
                    robot_print(f"[Plex API] Searching database for '{media_title}'...")
                    rating_key = None
                    
                    try:
                        # Query your local Plex server directly via its API
                        search_url = f"http://{plex_ip}:32400/search?query={urllib.parse.quote(media_title)}&X-Plex-Token={plex_token}"
                        plex_resp = requests.get(search_url, headers={"Accept": "application/json"}, timeout=5)
                        
                        if plex_resp.status_code == 200:
                            results = plex_resp.json().get("MediaContainer", {}).get("Metadata", [])
                            for item in results:
                                if item.get("type") == media_type:
                                    rating_key = item.get("ratingKey")
                                    robot_print(f"[Plex API] Found match! Rating Key: {rating_key}")
                                    break
                    except Exception as e:
                        robot_print(f"[Plex API] Connection failed: {e}")

                    if not rating_key:
                        error_msg = f"Could not find {media_title} in the library."
                        robot_print(f"[Plex API] {error_msg}")
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": tool_call.function.name, "content": error_msg})
                        continue

                    # --- THE BULLETPROOF DEEP LINK ---
                    
                    # Punch 1: Wake the TV up to the Home Screen
                    robot_print(f"[Home Assistant] Waking {target_tv} via ADB...")
                    call_home_assistant("androidtv/adb_command", {
                        "entity_id": target_tv,
                        "command": "input keyevent 3"
                    })
                    
                    time.sleep(2)
                    
                    # Punch 2: Inject the Deep Link Intent directly into the TV's processor
                    robot_print(f"[Home Assistant] Deep Linking directly to movie ID {rating_key} on {target_tv}...")
                    deep_link_cmd = f'am start -a android.intent.action.VIEW -n com.plexapp.android/com.plexapp.plex.activities.SplashActivity -d "plex://server://{plex_machine_id}/com.plexapp.plugins.library/library/metadata/{rating_key}"'
                    
                    result = call_home_assistant("androidtv/adb_command", {
                        "entity_id": target_tv,
                        "command": deep_link_cmd
                    })
                    
                    # FIX: Tell the AI the actual truth about the Home Assistant connection
                    if "Error" in result:
                        final_status = f"Failed to launch media. Home Assistant connection failed."
                    else:
                        final_status = f"Success: The media was launched directly on {target_tv}."
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "content": final_status
                    })
            second_response = client.chat.completions.create(
                model=gpt_model, 
                messages=messages, 
                max_tokens=max_tokens, 
                temperature=0.7
            )
            robot_print(f"[DEBUG] GPT Multi-Turn Tool Generation time: {time.time() - start_t1:.2f} seconds")
            return second_response.choices[0].message.content.strip()
            
        robot_print(f"[DEBUG] GPT Generation time: {time.time() - start_t1:.2f} seconds")
        return message.content.strip()

    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(fetch_gpt)

            is_thinking = True
            def keep_alive_ping():
                silence_packet = bytes([128] * 160)
                while is_thinking and call and call.state == CallState.ANSWERED:
                    try:
                        call.write_audio(silence_packet)
                        time.sleep(0.02)
                    except Exception: break
                    
            threading.Thread(target=keep_alive_ping, daemon=True).start()

            # Bumping timeout slightly to account for our 10 seconds of sleeping!
            full_response = future.result(timeout=40) 
            is_thinking = False 

            if full_response and full_response != "TRANSFER_TRIGGERED":
                generate_and_speak(call, full_response, wait=True)
                
            return full_response, messages

    except Exception as e:
        is_thinking = False
        error_msg = "I am sorry, but my connection to the central mainframe has been interrupted."
        generate_and_speak(call, error_msg, wait=True)
        return error_msg, messages

def generate_and_speak(call, text_to_speak, wait=False):
    clean_text = re.sub(r'[^\x00-\x7F]+', '', text_to_speak).strip()
    if not clean_text:
        return
        
    robot_print(f"🤖 ROBOT SAYS: \"{clean_text}\"")
    speed = get_config("voice_speed", 1.0)
    
    try:
        t2w_cmd = ["text2wave", "-eval", f"(Parameter.set 'Duration_Stretch {speed})", "-o", "-"]
        t2w_process = subprocess.Popen(t2w_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", "pipe:0",
            "-af", "acompressor=ratio=4,highpass=f=300,lowpass=f=3400,volume=1.6", 
            "-ar", "8000", "-ac", "1", "-f", "u8", "pipe:1"
        ]
        ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdin=t2w_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        
        t2w_process.stdin.write(clean_text.encode('utf-8'))
        t2w_process.stdin.close() 
        
        raw_audio_bytes, _ = ffmpeg_process.communicate()
        
        t2w_process.stdout.close()
        t2w_process.wait()
        ffmpeg_process.wait()
        
        if raw_audio_bytes:
            robot_print(f"[Rotary Robot] Streaming RAM audio directly to handset...")
            call.write_audio(raw_audio_bytes)
            
            if wait:
                play_time_seconds = len(raw_audio_bytes) / 8000.0
                stop_time = time.time() + play_time_seconds
                
                while time.time() <= stop_time and call.state == CallState.ANSWERED:
                    time.sleep(0.01) 
                
    except Exception as e:
        robot_print(f"[AI Brain] In-Memory TTS Error: {e}")

# --- MAIN CALL LOGIC ---
def answer_call(call):
    call_start_time = time.time()
    caller_id = call.request.headers.get('From', {}).get('number', 'Unknown')
    
    try:
        robot_print(f"\n[=========== INCOMING CALL: {caller_id} ===========]")
        call.answer()
        robot_print("[Rotary Robot] Call answered.")

        default_prompt = get_config("system_prompt", "You are a helpful AI.")
        default_greeting = get_config("default_greeting", "Greetings. The analog bridge is connected.")
        caller_overrides = get_config("caller_overrides", {})
        
        system_prompt = default_prompt
        greeting_text = default_greeting
        allow_ha = False
        
        if caller_id in caller_overrides:
            override = caller_overrides[caller_id]
            system_prompt = override.get("prompt", system_prompt)
            greeting_text = override.get("greeting", greeting_text)
            allow_ha = override.get("allow_ha", False)
            nickname = override.get("name", caller_id)
            robot_print(f"[Rotary Robot] Recognized caller: {nickname} ({caller_id})")

        current_time_str = datetime.now().strftime("%I:%M %p on %A, %B %d, %Y")
        local_weather_str = get_current_weather()
        
        if allow_ha:
            tool_instruction = " If asked to play a show, you MUST use the play_plex_media tool. Do not just say you will do it. If asked to call or transfer to a person, use the transfer_call tool."
            full_system_identity = f"{system_prompt}{tool_instruction} Current Local Time: {current_time_str}. Current Local Weather: {local_weather_str}."
        else:
            full_system_identity = f"{system_prompt} Current Local Time: {current_time_str}. Current Local Weather: {local_weather_str}."
            
        conversation_history = [{"role": "system", "content": full_system_identity}]

        generate_and_speak(call, greeting_text, wait=True)
        empty_responses = 0

        while call.state == CallState.ANSWERED:

            audio_data = record_audio_dynamic(call, silence_timeout=0.8) 

            if audio_data is not None:
                user_text = transcribe_audio(audio_data)
                if user_text:
                    empty_responses = 0
                    
                    clean_text = user_text.lower()
                    if "goodbye" in clean_text or "good bye" in clean_text or "hang up" in clean_text:
                        generate_and_speak(call, "Goodbye. Disconnecting the analog bridge.", wait=True)
                        break
                        
                    conversation_history.append({"role": "user", "content": user_text})
                    
                    # NOTE: Passed caller_id here so the tool knows who is making the request!
                    ai_response, conversation_history = query_and_stream_response(conversation_history, call, allow_ha=allow_ha, caller_id=caller_id)
                    
                    if ai_response == "TRANSFER_TRIGGERED":
                        # Wait in silence for 10 seconds to let the PBX physically rip the channel away.
                        # If successful, this will trigger an InvalidStateError and safely end the script.
                        time.sleep(10)
                        break
                        
                    conversation_history.append({"role": "assistant", "content": ai_response})
                else: empty_responses += 1
            else: empty_responses += 1

            if empty_responses >= 2:
                generate_and_speak(call, "I am not detecting any input. Ending transmission. Goodbye.", wait=True)
                break

        robot_print("[Rotary Robot] Interaction complete. Hanging up.")
        call.hangup()
        robot_print("[================================================]\n")

    except InvalidStateError: robot_print("[Rotary Robot] Line disconnected by caller.")
    except Exception as e: robot_print(f"[Rotary Robot] Unexpected error: {e}")
    finally:
        call_duration = round(time.time() - call_start_time, 1)
        log_call_history(caller_id, call_duration)
        robot_print(f"[Logger] Call saved. Duration: {call_duration}s")

# --- WEB UI LAUNCHER WRAPPER ---
def start_robot():
    robot_print("Initializing Rotary Robot SIP Engine...")
    phone = None
    
    while True:
        sip_server = get_config("sip_server")
        sip_user = get_config("sip_username")
        sip_pass = get_config("sip_password")
        my_ip = get_config("my_ip", "0.0.0.0")

        if not sip_server or not sip_user or not sip_pass:
            robot_print("[SYSTEM] SIP Credentials missing. Waiting for Web UI Setup...")
            time.sleep(10)
            continue

        try:
            phone = VoIPPhone(
                server=sip_server, 
                port=5060, 
                username=sip_user, 
                password=sip_pass,
                callCallback=answer_call,
                sipPort=5062,
                myIP=my_ip
            )
            phone.start()
            robot_print("Registration successful! Rotary Robot is online.")
            
            while True:
                time.sleep(10) 
        except Exception as e:
            robot_print(f"SIP Connection Failed. Retrying in 10s... Error: {e}")
            if phone:
                try: phone.stop()
                except: pass
            time.sleep(10)

if __name__ == "__main__":
    start_robot()
