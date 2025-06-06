import os
import random
import logging
from datetime import datetime
from typing import Dict, List, Optional
import sqlite3
from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

# Config logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_NAME = 'chat_users.db'

# App Configuration Settings
class Config:
    """Application configuration with secure defaults"""
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(24)
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*')
    
    # Available chat rooms - stored as constant for now, could be moved to database
    CHAT_ROOMS = [
        'General',
        'Video Games',
        'Movies',
        'Nerd Shit'
    ]

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Handle reverse proxy headers
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Initialize SocketIO with appropriate CORS settings
socketio = SocketIO(
    app,
    cors_allowed_origins=app.config['CORS_ORIGINS'],
    logger=True,
    engineio_logger=True
)

# In-memory storage for active users
active_users: Dict[str, dict] = {}

def generate_guest_username() -> str:
    """Generate a unique guest username with timestamp to avoid collisions"""
    timestamp = datetime.now().strftime('%H%M')
    return f'Guest{timestamp}{random.randint(1000,9999)}'

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))  # Redirect to login page

    return render_template(
        'index.html',
        username=session['username'],
        rooms=app.config['CHAT_ROOMS']
    )

# Login route.
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Fetch user from database
        user = get_user(username)
        if user and check_password_hash(user[2], password):
            session['username'] = username
            logger.info(f"User logged in: {username}")
            return redirect(url_for('index'))
        else:
            logger.warning("Invalid login attempt")
            return render_template('login.html', error="Invalid username or password.")
    
    return render_template('login.html')

# Signup route
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return render_template('signup.html', error="Please enter a username and password.")

        # Hash password
        hashed_pw = generate_password_hash(password)

        # Add user to the database
        success = add_user(username, hashed_pw)
        if success:
            session['username'] = username
            logger.info(f"User signed up: {username}")
            return redirect(url_for('index'))
        else:
            return render_template('signup.html', error="Username already exists.")
    
    return render_template('signup.html')

# Logout route
@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# Initialize SQLite Database
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                room TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp DATETIME NOT NULL
            )
        ''')
        conn.commit()

def get_user(username):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        return cursor.fetchone()

def add_user(username, password):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

# SocketIO Events
@socketio.event
def connect():
    try:
        if 'username' not in session:
            session['username'] = generate_guest_username()
        
        active_users[request.sid] = {
            'username': session['username'],
            'connected_at': datetime.now().isoformat()
        }
        
        emit('active_users', {
            'users': [user['username'] for user in active_users.values()]
        }, broadcast=True)
        
        logger.info(f"User connected: {session['username']}")
    
    except Exception as e:
        logger.error(f"Connection error: {str(e)}")
        return False

@socketio.event
def disconnect():
    try:
        if request.sid in active_users:
            username = active_users[request.sid]['username']
            del active_users[request.sid]
            
            emit('active_users', {
                'users': [user['username'] for user in active_users.values()]
            }, broadcast=True)
            
            logger.info(f"User disconnected: {username}")
    
    except Exception as e:
        logger.error(f"Disconnection error: {str(e)}")

@socketio.on('join')
def on_join(data: dict):
    try:
        username = session['username']
        room = data['room']
        
        if room not in app.config['CHAT_ROOMS']:
            logger.warning(f"Invalid room join attempt: {room}")
            return
        
        join_room(room)
        active_users[request.sid]['room'] = room
        
        # Retrieve messages from the database for the room
        messages = get_messages_from_db(room)
        
        # Emit the chat history to the user when they join the room
        emit('chat_history', {
            'messages': messages
        }, room=room)

        emit('status', {
            'msg': f'{username} has joined the room.',
            'type': 'join',
            'timestamp': datetime.now().isoformat()
        }, room=room)
        
        logger.info(f"User {username} joined room: {room}")
    
    except Exception as e:
        logger.error(f"Join room error: {str(e)}")

@socketio.on('message')
def handle_message(data: dict):
    try:
        username = session['username']
        room = data.get('room', 'General')
        msg_type = data.get('type', 'message')
        message = data.get('msg', '').strip()
        
        if not message:
            return
        
        timestamp = datetime.now().isoformat()

        # Save the message to the database
        save_message_to_db(username, room, message, timestamp)

        # If the message is a private message, handle it accordingly
        if msg_type == 'private':
            target_user = data.get('target')
            if not target_user:
                return
                
            for sid, user_data in active_users.items():
                if user_data['username'] == target_user:
                    emit('private_message', {
                        'msg': message,
                        'from': username,
                        'to': target_user,
                        'timestamp': timestamp
                    }, room=sid)
                    logger.info(f"Private message sent: {username} -> {target_user}")
                    return
                    
            logger.warning(f"Private message failed - user not found: {target_user}")
        
        else:
            # Regular room message
            if room not in app.config['CHAT_ROOMS']:
                logger.warning(f"Message to invalid room: {room}")
                return
                
            emit('message', {
                'msg': message,
                'username': username,
                'room': room,
                'timestamp': timestamp
            }, room=room)
            
            logger.info(f"Message sent in {room} by {username}")
    
    except Exception as e:
        logger.error(f"Message handling error: {str(e)}")


def save_message_to_db(username: str, room: str, message: str, timestamp: str):
    """Save the chat message to the database"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO chat_messages (username, room, message, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (username, room, message, timestamp))
        conn.commit()

def get_messages_from_db(room: str) -> List[Dict[str, str]]:
    """Retrieve previous messages from the database for the given room"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, message, timestamp
            FROM chat_messages
            WHERE room = ?
            ORDER BY timestamp ASC
        ''', (room,))
        rows = cursor.fetchall()
        
        messages = [{'username': row[0], 'message': row[1], 'timestamp': row[2]} for row in rows]
        
        return messages

# Initialize DB on startup
init_db()

if __name__ == '__main__':
    # In production, use gunicorn or uwsgi instead
    port = int(os.environ.get('PORT', 5000))
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=app.config['DEBUG'],
        use_reloader=app.config['DEBUG']
    )
