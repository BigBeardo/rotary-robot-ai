import time
import wave
import subprocess
import requests
import json
import threading
import queue
import os
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
    bar_length = min(int(deviation), 40)
    if deviation > 0.1: robot_print(f"[MIC LEVEL] {deviation:5.1f} |{'█' * bar_length}")
    return deviation < threshold

def play_audio(call, file_path):
    """Kept specifically to play the pre-recorded greeting.wav file."""
    try:
        with wave.open(file_path, 'rb') as f:
            frames = f.getnframes()
            audio_data = f.readframes(frames)
        robot_print(f"[Rotary Robot] Transmitting {file_path} to handset...")
        call.write_audio(audio_data)
        stop_time = time.time() + (frames / 8000.0)
        while time.time() <= stop_time and call.state == CallState.ANSWERED:
            time.sleep(0.1)
    except Exception as e:
        robot_print(f"[Rotary Robot] Failed to play audio: {e}")

def flush_audio_buffer(call):
    cleared_chunks = 0
    while call.state == CallState.ANSWERED:
        start_time = time.time()
        try:
            chunk = call.read_audio(160)
            cleared_chunks += 1
            if time.time() - start_time > 0.01: break
        except Exception: break
    if cleared_chunks > 1: robot_print(f"[DEBUG] Flushed {cleared_chunks} stale packets.")

def record_audio_dynamic(call, raw_file="incoming_raw.wav", clean_file="incoming_ready.wav", silence_timeout=1.0, max_duration=15):
    robot_print("[Rotary Robot] Listening... (MULTI-THREADED MODE)")
    audio_queue = queue.Queue()
    is_recording = True
    
    def audio_reader():
        while is_recording and call.state == CallState.ANSWERED:
            try:
                chunk = call.read_audio(160)
                if chunk: audio_queue.put(chunk)
            except Exception: break
                
    threading.Thread(target=audio_reader, daemon=True).start()
    
    try:
        with wave.open(raw_file, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(1)       
            wf.setframerate(8000)    
            
            start_time = time.time()
            last_speech_time = time.time()
            has_spoken = False
            
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
                        robot_print("[Rotary Robot] No initial speech detected.")
                        break
                        
                try:
                    chunk = audio_queue.get(timeout=0.05)
                    wf.writeframes(chunk)
                    if not is_silent(chunk):
                        has_spoken = True
                        last_speech_time = time.time()
                except queue.Empty: pass
    finally:
        is_recording = False 
        
    subprocess.run(["ffmpeg", "-y", "-i", raw_file, "-af", "volume=4.0", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", clean_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return clean_file if has_spoken else None

# --- AI BRAIN ---
def transcribe_audio(file_path):
    robot_print("[AI Brain] Decoding speech locally...")
    try:
        segments, info = whisper_model.transcribe(file_path, beam_size=5)
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
    robot_print(f"[AI Brain] Consulting GPT (Streaming)...")
    openai_key = get_config("openai_api_key")
    if not openai_key:
        error_msg = "Please configure my API keys in the dashboard."
        generate_and_speak(call, error_msg)
        return error_msg, messages

    client = OpenAI(api_key=openai_key)
    gpt_model = get_config("gpt_model", "gpt-4o")
    max_tokens = int(get_config("max_tokens", 150))
    
    is_thinking = True
    def keep_alive_ping():
        silence_packet = bytes([128] * 160)
        while is_thinking and call and call.state == CallState.ANSWERED:
            try:
                call.write_audio(silence_packet)
                time.sleep(0.02)
            except Exception: break
                
    threading.Thread(target=keep_alive_ping, daemon=True).start()
    
    try:
        start_t1 = time.time()
        response = client.chat.completions.create(model=gpt_model, messages=messages, max_tokens=max_tokens, temperature=0.7, stream=True)
        
        sentence_buffer = ""
        full_response = ""
        first_sentence = True
        
        for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                sentence_buffer += content
                full_response += content
                is_thinking = False 
                
                if any(punct in content for punct in ['.', '!', '?', ',']):
                    text_to_speak = sentence_buffer.strip()
                    sentence_buffer = ""
                    if first_sentence:
                        robot_print(f"[DEBUG] Time to first audio chunk: {time.time() - start_t1:.2f} seconds")
                        first_sentence = False
                    generate_and_speak(call, text_to_speak)
                    
        if sentence_buffer.strip(): generate_and_speak(call, sentence_buffer.strip())
        return full_response, messages

    except Exception as e:
        is_thinking = False
        error_msg = "I am sorry, but my connection to the central mainframe has been interrupted."
        generate_and_speak(call, error_msg)
        return error_msg, messages

def generate_and_speak(call, text_to_speak):
    robot_print(f"🤖 ROBOT SAYS: \"{text_to_speak}\"")
    speed = get_config("voice_speed", 1.0)
    
    try:
        # 1. Start text2wave, telling it to read from Python and output to RAM (-)
        t2w_cmd = ["text2wave", "-eval", f"(Parameter.set 'Duration_Stretch {speed})", "-o", "-"]
        t2w_process = subprocess.Popen(t2w_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        
        # 2. Start FFmpeg, telling it to read from text2wave's RAM output and format it for the phone line
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", "pipe:0",
            "-af", "acompressor=ratio=4,highpass=f=300,lowpass=f=3400,volume=1.6", 
            "-ar", "8000", "-ac", "1", "-f", "u8", "pipe:1"
        ]
        ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdin=t2w_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        
        # 3. Inject the text into the pipeline and grab the resulting audio bytes
        t2w_process.stdin.write(text_to_speak.encode('utf-8'))
        t2w_process.stdin.close() 
        raw_audio_bytes, _ = ffmpeg_process.communicate()
        
        # 4. Stream the bytes directly into the FreePBX telephone line
        if raw_audio_bytes:
            robot_print(f"[Rotary Robot] Streaming RAM audio directly to handset...")
            call.write_audio(raw_audio_bytes)
            
            # Keep the thread alive while the audio plays (8000 samples per sec, 1 byte per sample)
            play_time_seconds = len(raw_audio_bytes) / 8000.0
            stop_time = time.time() + play_time_seconds
            
            while time.time() <= stop_time and call.state == CallState.ANSWERED:
                time.sleep(0.05)
                
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
            flush_audio_buffer(call)
            clean_audio_file = record_audio_dynamic(call, silence_timeout=0.4)
            
            if clean_audio_file:
                user_text = transcribe_audio(clean_audio_file)
                if user_text:
                    empty_responses = 0
                    if "goodbye" in user_text.lower() or "hang up" in user_text.lower():
                        generate_and_speak(call, "Goodbye. Disconnecting the analog bridge.")
                        break
                    conversation_history.append({"role": "user", "content": user_text})
                    ai_response, conversation_history = query_and_stream_response(conversation_history, call)
                    conversation_history.append({"role": "assistant", "content": ai_response})
                else: empty_responses += 1
            else: empty_responses += 1
                
            if empty_responses >= 2:
                generate_and_speak(call, "I am not detecting any input. Ending transmission. Goodbye.")
                break
                
        robot_print("[Rotary Robot] Interaction complete. Hanging up.")
        call.hangup()
        robot_print("[================================================]\n")
        
    except InvalidStateError: robot_print("[Rotary Robot] Error: The caller hung up early.")
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
