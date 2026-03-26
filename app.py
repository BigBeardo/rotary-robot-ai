from flask import Flask, render_template, render_template_string, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import threading
import json
import time
import os
import csv
import logging
from rotaryrobot_voip import start_robot, query_gpt4o

# Mute Flask's built-in terminal spam so we only see Robot logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

os.makedirs("data", exist_ok=True)

app = Flask(__name__)
app.secret_key = "rotary_robot_secure_session_key" 
CONFIG_FILE = "data/config.json"
LOG_FILE = "data/robot.log"
HISTORY_FILE = "data/call_history.csv"

# --- EMBEDDED HTML FOR LOGIN/SETUP ---
AUTH_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rotary Robot - Auth</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 flex items-center justify-center h-screen font-sans">
    <div class="bg-gray-800 p-8 rounded-2xl shadow-xl border border-gray-700 w-full max-w-md">
        <div class="flex justify-center mb-6">
            <div class="w-12 h-12 bg-blue-500 rounded-full flex items-center justify-center animate-pulse shadow-[0_0_15px_rgba(59,130,246,0.5)]"></div>
        </div>
        <h2 class="text-2xl font-bold text-white text-center mb-2">{{ title }}</h2>
        <p class="text-gray-400 text-center mb-6 text-sm">{{ subtitle }}</p>
        
        {% if error %}<div class="bg-red-500/20 border border-red-500 text-red-300 px-4 py-2 rounded-lg mb-4 text-sm text-center">{{ error }}</div>{% endif %}
        
        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-gray-400 text-sm mb-1">Username</label>
                <input type="text" name="username" required class="w-full bg-gray-900 border border-gray-600 rounded-xl p-3 text-white focus:outline-none focus:border-blue-500">
            </div>
            <div>
                <label class="block text-gray-400 text-sm mb-1">Password</label>
                <input type="password" name="password" required class="w-full bg-gray-900 border border-gray-600 rounded-xl p-3 text-white focus:outline-none focus:border-blue-500">
            </div>
            <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-3 rounded-xl transition duration-200 mt-4">{{ button_text }}</button>
        </form>
    </div>
</body>
</html>
"""

def load_config():
    if not os.path.exists(CONFIG_FILE): return {}
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def is_setup_complete():
    config = load_config()
    return "admin_user" in config and "admin_pass_hash" in config

@app.before_request
def require_login():
    allowed_routes = ['login', 'setup', 'static']
    if request.endpoint in allowed_routes:
        return
    if not is_setup_complete():
        return redirect(url_for('setup'))
    if 'logged_in' not in session:
        return redirect(url_for('login'))

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if is_setup_complete(): return redirect(url_for('login'))
    if request.method == 'POST':
        config = load_config()
        config['admin_user'] = request.form['username']
        config['admin_pass_hash'] = generate_password_hash(request.form['password'])
        save_config(config)
        session['logged_in'] = True
        return redirect(url_for('index'))
    return render_template_string(AUTH_HTML, title="System Initialization", subtitle="Create your secure Admin account.", button_text="Initialize System")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not is_setup_complete(): return redirect(url_for('setup'))
    error = None
    if request.method == 'POST':
        config = load_config()
        if request.form['username'] == config.get('admin_user') and check_password_hash(config.get('admin_pass_hash', ''), request.form['password']):
            session['logged_in'] = True
            return redirect(url_for('index'))
        error = "Invalid connection credentials."
    return render_template_string(AUTH_HTML, title="Secure Node Access", subtitle="Please authenticate to access the dashboard.", button_text="Establish Connection", error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/config', methods=['GET', 'POST'])
def config():
    if request.method == 'POST':
        current_config = load_config()
        for key, value in request.json.items():
            current_config[key] = value
        save_config(current_config)
        return jsonify({"status": "success"})
    
    safe_config = load_config().copy()
    safe_config.pop("admin_pass_hash", None)
    return jsonify(safe_config)

@app.route('/api/call_history', methods=['GET'])
def call_history():
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                reader = csv.DictReader(f)
                for row in reader: history.append(row)
        except Exception: pass
    return jsonify(history[::-1][:15]) # Return only the 15 most recent to save memory

@app.route('/api/simulate', methods=['POST'])
def simulate():
    history = request.json.get('history', [])
    config_data = load_config()
    from datetime import datetime
    system_content = f"{config_data.get('system_prompt', '')} The current date is {datetime.now().strftime('%B %d, %Y')}."
    messages = [{"role": "system", "content": system_content}] + history
    reply, _ = query_gpt4o(messages)
    return jsonify({"reply": reply})

@app.route('/stream_logs', methods=['GET'])
def stream_logs():
    # Robust JSON polling instead of glitchy SSE
    if not os.path.exists(LOG_FILE):
        return jsonify({"logs": "Waiting for robot initialization...\n"})
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
            # Send the last 40 lines to keep the box clean
            return jsonify({"logs": "".join(lines[-40:])})
    except Exception as e:
        return jsonify({"logs": f"Error reading logs: {e}\n"})

if __name__ == '__main__':
    print("Starting VoIP Background Thread...")
    voip_thread = threading.Thread(target=start_robot, daemon=True)
    voip_thread.start()
    print("Starting Web Dashboard on port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
