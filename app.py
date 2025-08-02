from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import os
import subprocess
import docker
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

# --- Configuração do Flask-Login e Banco de Dados (sem mudanças aqui) ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

DATABASE = 'users.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            );
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                status TEXT NOT NULL,
                user_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
        ''')
        cursor = db.execute("SELECT * FROM users WHERE username = 'admin'")
        user_exists = cursor.fetchone()
        if not user_exists:
            hashed_password = generate_password_hash('adminpass', method='pbkdf2:sha256')
            db.execute("INSERT INTO users (username, password) VALUES (?, ?)", ('admin', hashed_password))
            db.commit()
            print("Usuário 'admin' com senha 'adminpass' criado no banco de dados.")
        db.close()

class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password

    @staticmethod
    def get(user_id):
        db = get_db()
        user_data = db.execute("SELECT id, username, password FROM users WHERE id = ?", (user_id,)).fetchone()
        db.close()
        if user_data:
            return User(user_data['id'], user_data['username'], user_data['password'])
        return None

    @staticmethod
    def get_by_username(username):
        db = get_db()
        user_data = db.execute("SELECT id, username, password FROM users WHERE username = ?", (username,)).fetchone()
        db.close()
        if user_data:
            return User(user_data['id'], user_data['username'], user_data['password'])
        return None

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# --- Fim da Configuração do Flask-Login e Banco de Dados ---

UPLOAD_FOLDER = '/shared_volume'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

#client = docker.from_env()

# --- Função para iniciar o ttyd em segundo plano (com --writable) ---
def start_ttyd():
    try:
        print("Iniciando ttyd na porta 7681 com modo gravável...")
        # Adiciona o parâmetro --writable
        subprocess.Popen(["ttyd", "-p", "7681", "--writable", "bash", "-c", "cd /shared_volume && bash"], preexec_fn=os.setsid)
        print("ttyd iniciado.")
    except Exception as e:
        print(f"Erro ao iniciar ttyd: {e}")

# --- Rotas (sem mudanças aqui) ---
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.get_by_username(username)
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash('Login realizado com sucesso!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Nome de usuário ou senha inválidos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('login'))

@app.route('/shell')
@login_required
def shell():
    return render_template('shell.html')

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_file():
    db = get_db()
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('Nenhum arquivo selecionado.', 'warning')
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            flash('Nenhum arquivo selecionado.', 'warning')
            return redirect(request.url)
        if file:
            filename = file.filename
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            try:
                file.save(filepath)
                status = "Sucesso"
                flash(f'Arquivo "{filename}" enviado com sucesso para o volume compartilhado!', 'success')
            except Exception as e:
                status = "Falha"
                flash(f'Erro ao enviar o arquivo "{filename}": {e}', 'danger')

            db.execute(
                "INSERT INTO uploads (filename, upload_time, status, user_id) VALUES (?, ?, ?, ?)",
                (filename, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), status, current_user.id)
            )
            db.commit()
            db.close()
            return redirect(url_for('upload_file'))

    recent_uploads = db.execute(
        "SELECT filename, upload_time, status FROM uploads WHERE user_id = ? ORDER BY upload_time DESC LIMIT 10",
        (current_user.id,)
    ).fetchall()
    db.close()

    return render_template('upload.html', uploads=recent_uploads)

@app.route('/logs')
@login_required
def view_logs():
    try:
        local_client = docker.from_env()
    except docker.errors.DockerException as e:
        flash(f"Erro ao conectar ao Docker Daemon: {e}. Verifique se o docker.sock está montado.", "danger")
        return render_template('logs.html', logs="Erro: Não foi possível conectar ao Docker Daemon.", container_name="N/A")
    
    container_name = 'main_application'
    logs = "Nenhum log encontrado ou container não está rodando."
    try:
        container = client.containers.get(container_name)
        logs = container.logs().decode('utf-8')
    except docker.errors.NotFound:
        logs = f"Container '{container_name}' não encontrado."
    except Exception as e:
        logs = f"Erro ao obter logs: {e}"

    return render_template('logs.html', logs=logs, container_name=container_name)

if __name__ == '__main__':
    init_db()
    ttyd_thread = threading.Thread(target=start_ttyd)
    ttyd_thread.daemon = True
    ttyd_thread.start()

    app.run(debug=True, host='0.0.0.0')