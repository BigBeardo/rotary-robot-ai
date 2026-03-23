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

# --- AUDIO PIPELINE ---
def is_silent(chunk, threshold=1.0): 
    if not chunk: return True
    deviation = sum(abs(b - 128) for b in chunk) / len(chunk)
    
    if deviation >= threshold:
        bar_length = min(int(deviation), 40)
        robot_print(f"[MIC LEVEL] {deviation:5.1f} |{'█' * bar_length}")
        
    return deviation < threshold

def play_audio(call, file_path):
    try:
        with wave.open(file_path, 'rb') as f:
            frames = f.getnframes()
            audio_data = f.readframes(frames)
        robot_print(f"[Rotary Robot] Transmitting {file_path} to handset...")
        call.write_audio(audio_data)
    except Exception as e:
        robot_print(f"[Rotary Robot] Failed to play audio: {e}")

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

def query_and_stream_response(messages, call):
    robot_print(f"[AI Brain] Consulting GPT (Fluid Background Mode)...")
    openai_key = get_config("openai_api_key")
    if not openai_key:
        error_msg = "Please configure my API keys in the dashboard."
        generate_and_speak(call, error_msg)
        return error_msg, messages

    client = OpenAI(api_key=openai_key)
    gpt_model = get_config("gpt_model", "gpt-4o")
    max_tokens = int(get_config("max_tokens", 150))

    def fetch_gpt():
        start_t1 = time.time()
        response = client.chat.completions.create(
            model=gpt_model, 
            messages=messages, 
            max_tokens=max_tokens, 
            temperature=0.7
        )
        robot_print(f"[DEBUG] GPT Generation time: {time.time() - start_t1:.2f} seconds")
        return response.choices[0].message.content.strip()

    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(fetch_gpt)

            fillers = ["Processing.", "Accessing mainframe.", "Working.", "Query received.", "Calculating."]
            generate_and_speak(call, random.choice(fillers))

            is_thinking = True
            def keep_alive_ping():
                silence_packet = bytes([128] * 160)
                while is_thinking and call and call.state == CallState.ANSWERED:
                    try:
                        call.write_audio(silence_packet)
                        time.sleep(0.02)
                    except Exception: break
                    
            threading.Thread(target=keep_alive_ping, daemon=True).start()

            full_response = future.result(timeout=15)
            is_thinking = False 

            if full_response:
                generate_and_speak(call, full_response)
                
            return full_response, messages

    except Exception as e:
        is_thinking = False
        error_msg = "I am sorry, but my connection to the central mainframe has been interrupted."
        generate_and_speak(call, error_msg)
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

        system_prompt = get_config("system_prompt", "You are a helpful AI.")
        current_time_str = datetime.now().strftime("%I:%M %p on %A, %B %d, %Y")
        local_weather_str = get_current_weather()

        full_system_identity = f"{system_prompt} Current Local Time: {current_time_str}. Current Local Weather: {local_weather_str}."
        conversation_history = [{"role": "system", "content": full_system_identity}]

        play_audio(call, "greeting_u8.wav")
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
                    ai_response, conversation_history = query_and_stream_response(conversation_history, call)
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
