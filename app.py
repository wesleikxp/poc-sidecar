import os
import subprocess
import threading
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# --- Para o SFTP (Paramiko) ---
import paramiko
import socket
import logging
# logging.basicConfig(level=logging.DEBUG) # Descomente para debug do Paramiko

# --- Configurações SFTP ---
SFTP_PORT = 2222 # Porta para o servidor SFTP
SFTP_HOST = '0.0.0.0'
SFTP_USERNAME = 'sftpuser'
SFTP_PASSWORD = 'sftppass' # EM PRODUÇÃO: USE SENHAS MAIS ROBUSTAS OU CHAVES SSH!
SFTP_DATA_DIR = '/shared_volume' # Onde os arquivos do SFTP serão armazenados

# Removido a geração da chave AQUI! Ela será movida para o if __name__ == '__main__':

class SFTPServer(paramiko.ServerInterface):
    """
    Implementa a interface do servidor SFTP para Paramiko.
    """
    def __init__(self):
        self.event = threading.Event()

    def check_auth_password(self, username, password):
        if (username == SFTP_USERNAME) and (password == SFTP_PASSWORD):
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == 'session':
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def get_allowed_auths(self, username):
        return 'password,publickey'

    def check_port_forward_request(self, address, port):
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_subsystem_request(self, name, channel):
        if name == 'sftp':
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_shell_request(self, channel):
        return False

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        return False

def start_sftp_server():
    """Inicia o servidor SFTP."""
    # A chave de host será gerada no main antes desta thread iniciar.
    HOST_KEY_PATH = os.path.join(SFTP_DATA_DIR, 'sftp_host_key') # Definir aqui também para uso dentro da função

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        sock.bind((SFTP_HOST, SFTP_PORT))
        sock.listen(10)
        print(f"SFTP Server listening on {SFTP_HOST}:{SFTP_PORT}...")

        while True:
            # Em produção, um loop de select ou um ThreadPoolExecutor é melhor.
            # Aqui, para simplicidade, um accept bloqueante está ok.
            try:
                conn, addr = sock.accept()
            except BlockingIOError:
                # Nenhuma conexão, espera um pouco para não consumir CPU
                threading.Event().wait(0.1)
                continue

            print(f"SFTP Connection from {addr}")
            t = paramiko.Transport(conn)
            # Carrega a chave de host que já deve ter sido criada
            try:
                host_key = paramiko.RSAKey.from_private_key_file(HOST_KEY_PATH)
                t.add_server_key(host_key)
            except Exception as key_e:
                print(f"Erro ao carregar chave de host SFTP: {key_e}. Certifique-se de que {HOST_KEY_PATH} existe e tem permissões corretas.")
                t.close()
                continue

            server = SFTPServer()
            try:
                t.start_server(server=server)
                sftp_channel = t.open_session()
                if sftp_channel.max_packet_size < 1024:
                    sftp_channel.max_packet_size = 1024
                sftp_server = paramiko.SFTP_Server(sftp_channel, paramiko.SFTPServer, sod_root=SFTP_DATA_DIR)
                sftp_server.serve_forever()
            except Exception as e:
                print(f"SFTP session error: {e}")
            finally:
                if t.is_active():
                    t.close()

    except Exception as e:
        print(f"Failed to start SFTP server: {e}")


# --- Configurações do Flask ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

# --- Configuração do Flask-Login e Banco de Dados ---
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

UPLOAD_FOLDER = '/shared_volume'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# O diretório /shared_volume será montado em tempo de execução
# mas sua existência como ponto de montagem deve ser garantida no Dockerfile
# ou na configuração do volume para que os arquivos possam ser escritos.
# Criamos o diretório aqui caso ele ainda não exista no FS do container,
# antes da montagem do volume externo (o que o Docker faz por si só).
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def start_ttyd():
    try:
        print(f"Iniciando ttyd na porta 7681 com modo gravável, no diretório {SFTP_DATA_DIR}...")
        subprocess.Popen(["ttyd", "-p", "7681", "--writable", "bash", "-c", f"cd {SFTP_DATA_DIR} && bash"], preexec_fn=os.setsid)
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
            db.close()
            return redirect(url_for('upload_file'))

    db.close()
    return render_template('upload.html')

if __name__ == '__main__':
    init_db()

    # --- NOVO: Gerar a chave de host SFTP AQUI, em tempo de execução ---
    HOST_KEY_PATH = os.path.join(SFTP_DATA_DIR, 'sftp_host_key')
    if not os.path.exists(os.path.dirname(HOST_KEY_PATH)): # Garante que o diretório base exista
        os.makedirs(os.path.dirname(HOST_KEY_PATH), exist_ok=True)

    if not os.path.exists(HOST_KEY_PATH):
        try:
            print(f"Gerando chave de host SFTP em {HOST_KEY_PATH}...")
            paramiko.RSAKey.generate(2048).write_private_key_file(HOST_KEY_PATH)
            print("Chave de host SFTP gerada com sucesso.")
        except Exception as e:
            print(f"Erro ao gerar chave de host SFTP: {e}")
            # Em um cenário real, você pode querer abortar ou ter um fallback
            # Aqui, o SFTP server falharia ao iniciar se a chave não puder ser carregada.

    # Inicia o ttyd em uma thread separada
    ttyd_thread = threading.Thread(target=start_ttyd)
    ttyd_thread.daemon = True
    ttyd_thread.start()

    # Inicia o servidor SFTP em uma thread separada
    sftp_thread = threading.Thread(target=start_sftp_server)
    sftp_thread.daemon = True
    sftp_thread.start()

    app.run(debug=True, host='0.0.0.0')