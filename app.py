from flask import Flask, render_template, send_from_directory, jsonify, request, session, abort, make_response, flash, redirect, url_for
from flask_socketio import SocketIO, emit
from flask_cors import CORS, cross_origin
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String
from flask_login import UserMixin, login_user, LoginManager, login_required, current_user, logout_user
import uuid
import logging
import time
import os
import json
import random
import asyncio
import base64

from music_downloader import maindownload

app = Flask(__name__)
cors = CORS(app)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*",
                    logger=True, engineio_logger=True)


class Base(DeclarativeBase):
    pass

app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///users.db"
db = SQLAlchemy(model_class=Base)
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.get_or_404(User, user_id)


class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.String(36), primary_key=True)  # Ensure this is a String
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), nullable=False)


with app.app_context():
    db.create_all()


global current_timestamp, is_playing, queue, current_song_index, last_update_time
current_timestamp = 0
is_playing = False
queue = []
current_song_index = 0
last_update_time = time.time()

logging.basicConfig(level=logging.DEBUG)

LIBRARY_FOLDER = 'library'
LIBDATA_FILE = 'libdata.json'


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/radioQueue')
def mainpage():
    return render_template('radioQueue.html')

@app.route('/register', methods=["POST"])
def register():
    if request.method == "POST":
        email = request.form.get('email')
        result = db.session.execute(db.select(User).where(User.email == email))
        user = result.scalar()
        if user:
            flash("This email is already registered. Please use a different email or log in.")
            return redirect(url_for('register'))
        hash_and_salted_password = generate_password_hash(
            request.form.get('password'),
            method='pbkdf2:sha256',
            salt_length=8
        )
        new_user = User(
            id=str(uuid.uuid4()),
            email=request.form.get('email'),
            password=hash_and_salted_password,
            name=request.form.get('name'),
            role=request.form.get('role')
        )
        db.session.add(new_user)
        db.session.commit()
        response = {
            "message": "Login successful",
            "user": {
                "id": new_user.id,
                "email": new_user.email,
                "name": new_user.name,
                "role": new_user.role
            }
        }
        return make_response(jsonify(response), 200)

@app.route('/login', methods=["POST", "GET"])
def login():
    if request.method == "POST":
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        result = db.session.execute(db.select(User).where(User.email == email))
        user = result.scalar()
        if not user:
            return jsonify({"message": "That email does not exist, please try again."}), 400
            
        elif not check_password_hash(user.password, password):
            flash('Password incorrect, please try again.')
            return jsonify({"message": "Password incorrect, please try again."}), 400
        else:
            login_user(user)
            response = {
                "message": "Login successful",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "role": user.role
                }
            }
            return make_response(jsonify(response), 200)
    return render_template("login.html")

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/music/<encoded_path>')
def music(encoded_path):
    try:
        decoded_path = base64.urlsafe_b64decode(encoded_path.encode('utf-8')).decode('utf-8')
        directory = os.path.dirname(decoded_path)
        file_name = os.path.basename(decoded_path)
        logging.debug(f"Sending music file: {file_name} from {directory}")
        return send_from_directory(directory, file_name, as_attachment=True)
    except Exception as e:
        logging.error(f"Error decoding path: {e}")
        abort(404)


@app.route('/clear_library')
def clear_library():
    global queue
    library = open(LIBDATA_FILE, 'w')
    library.write('[]')
    library.close()
    file_list = os.listdir(LIBRARY_FOLDER)
    for file_name in file_list:
        file_path = os.path.join(LIBRARY_FOLDER, file_name)
        os.remove(file_path)
    queue = []
    return jsonify({'status': 'success'})


@app.route('/library')
def get_library():
    global queue
    return jsonify(queue)


@app.route('/add_song', methods=['POST'])
def add_song():
    url = request.json['songurl']
    logging.debug(f"Adding song from URL: {url}")
    asyncio.run(maindownload(url))
    time.sleep(5)
    return jsonify({'status': 'success'})


@app.route('/update_queue')
def update_queue():
    global queue
    with open(LIBDATA_FILE, 'r', encoding='utf-8') as file:
        libdata = json.load(file)
    existing_urls = {song['url'] for song in queue}
    new_songs = [song for song in libdata if song['url'] not in existing_urls]
    queue.extend(new_songs)
    return jsonify({'status': 'success', 'new_queue': queue})


def get_current_song():
    if queue and 0 <= current_song_index < len(queue):
        return queue[current_song_index]
    return None


@socketio.on('connect')
def handle_connect():
    global current_timestamp, is_playing, last_update_time, queue, current_song_index
    logging.debug("Client connected")
    current_song = get_current_song()
    if current_song:
        emit('sync', {
            'timestamp': current_timestamp,
            'is_playing': is_playing,
            'song': {
                'filename': current_song['path'],
                'title': current_song['name'],
                'artist': current_song['artist'],
                'cover_art': current_song['cover_url']
            }
        })
    else:
        emit('sync', {'timestamp': current_timestamp,
             'is_playing': is_playing})


@socketio.on('play')
def handle_play(data):
    global is_playing, current_timestamp, last_update_time
    logging.debug(f"Play event received with data: {data}")
    if 'timestamp' in data:
        current_timestamp = data['timestamp']
        logging.debug(f"Updated current_timestamp to: {current_timestamp}")
    else:
        logging.warning("Timestamp not found in play data")
    is_playing = True
    last_update_time = time.time()
    emit('play', {'timestamp': current_timestamp}, broadcast=True)

@socketio.on('pause')
def handle_pause(data):
    global is_playing, current_timestamp, last_update_time
    logging.debug(f"Pause event received with data: {data}")
    if 'timestamp' in data:
        current_timestamp = data['timestamp']
        logging.debug(f"Updated current_timestamp to: {current_timestamp}")
    else:
        logging.warning("Timestamp not found in pause data")
    is_playing = False
    last_update_time = time.time()
    emit('pause', {'timestamp': current_timestamp}, broadcast=True)

@socketio.on('request_sync')
def handle_request_sync():
    global current_timestamp, is_playing, last_update_time
    logging.debug("Sync request received")
    emit('sync', {'timestamp': current_timestamp, 'is_playing': is_playing})

@socketio.on('sync')
def handle_sync(data):
    global current_timestamp, is_playing, last_update_time
    logging.debug(f"Sync event received with data: {data}")
    if 'timestamp' in data:
        current_timestamp = data['timestamp']
        logging.debug(f"Updated current_timestamp to: {current_timestamp}")
    else:
        logging.warning("Timestamp not found in sync data")
    if 'is_playing' in data:
        is_playing = data['is_playing']
    else:
        logging.warning("is_playing not found in sync data")
    last_update_time = time.time()
    emit('sync', {'timestamp': current_timestamp,
         'is_playing': is_playing}, broadcast=True)

@socketio.on('timestamp')
def handle_timestamp(data):
    global current_timestamp, last_update_time
    logging.debug(f"Timestamp received from client: {data}")
    if 'timestamp' in data:
        current_timestamp = data['timestamp']
        last_update_time = time.time()
        logging.debug(f"Updated server timestamp to: {current_timestamp}")
        # Ensure broadcast of the latest state
        emit('sync', {'timestamp': current_timestamp,
             'is_playing': is_playing}, broadcast=True)

@socketio.on('seek')
def handle_seek(data):
    global current_timestamp, last_update_time, is_playing
    logging.debug(f"Seek event received with timestamp: {data['timestamp']}")
    if 'timestamp' in data:
        current_timestamp = data['timestamp']
        last_update_time = time.time()
        logging.debug(f"Updated current_timestamp to: {current_timestamp}")
        emit('sync', {'timestamp': current_timestamp, 'is_playing': is_playing, 'action': 'seek'}, broadcast=True)
    else:
        logging.warning("Timestamp not found in seek data")

@socketio.on('next_song')
def handle_next_song():
    global current_song_index, queue, current_timestamp, is_playing, last_update_time
    logging.debug("Next song requested")
    current_song_index += 1
    if current_song_index >= len(queue):
        current_song_index = 0
    if queue:
        next_song = queue[current_song_index]
        is_playing = True
        last_update_time = time.time()
        emit('next_song', {
            'filename': next_song['path'],
            'title': next_song['name'],
            'artist': next_song['artist'],
            'cover_art': next_song['cover_url']
        }, broadcast=True)
    else:
        is_playing = False
        emit('pause', {'timestamp': 0}, broadcast=True)

@socketio.on('prev_song')
def handle_prev_song():
    global current_song_index, queue, current_timestamp, is_playing, last_update_time
    logging.debug("Previous song requested")
    current_song_index -= 1
    if current_song_index < 0:
        current_song_index = len(queue) - 1
    if queue:
        prev_song = queue[current_song_index]
        is_playing = True
        last_update_time = time.time()
        emit('prev_song', {
            'filename': prev_song['path'],
            'title': prev_song['name'],
            'artist': prev_song['artist'],
            'cover_art': prev_song['cover_url']
        }, broadcast=True)
    else:
        is_playing = False
        emit('pause', {'timestamp': 0}, broadcast=True)

@socketio.on('select_song')
def handle_select_song(data):
    global current_song_index, queue, current_timestamp, last_update_time, is_playing
    logging.debug(f"Song selected: {data}")
    current_song_index = data['index']
    if current_song_index < len(queue):
        selected_song = queue[current_song_index]
        is_playing = True
        last_update_time = time.time()
        try:
            emit('song_selected', {
                'filename': selected_song['path'],
                'title': selected_song['name'],
                'artist': selected_song['artist'],
                'cover_art': selected_song['cover_url']
            }, broadcast=True)
        except Exception as e:
            logging.error(f"Error selecting song: {e}")
            get_dir = os.listdir(LIBRARY_FOLDER)
            for file in get_dir:
                if selected_song['name'] in file:
                    emit('song_selected', {
                        'filename': os.path.join(LIBRARY_FOLDER, file),
                        'title': selected_song['name'],
                        'artist': selected_song['artist'],
                        'cover_art': selected_song['cover_url']
                    }, broadcast=True)
    else:
        logging.error("Selected song index out of range.")


def load_library():
    global queue
    if not os.path.exists(LIBRARY_FOLDER):
        os.makedirs(LIBRARY_FOLDER)
    if not os.path.exists(LIBDATA_FILE):
        with open(LIBDATA_FILE, 'x', encoding='utf-8') as file:
            file.write('[]')
        queue = []
    else:
        with open(LIBDATA_FILE, 'r', encoding='utf-8') as file:
            queue = json.load(file)

if __name__ == '__main__':
    load_library()
    socketio.run(app, host='0.0.0.0', port=5135, allow_unsafe_werkzeug=True, debug=True)
