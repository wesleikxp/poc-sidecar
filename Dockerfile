# Usa uma imagem base que já tenha Python
FROM python:3.9-slim-bullseye

# Instala o ttyd
RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential \
  libssl-dev \
  libffi-dev \
  python3-dev \
  sudo \
  curl \
  ca-certificates \
  gnupg \
  wget \
  vim \
  && rm -rf /var/lib/apt/lists/*

RUN wget -O /tmp/ttyd https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 \
  && chmod +x /tmp/ttyd \
  && mv /tmp/ttyd /usr/local/bin

WORKDIR /app

# Copia os arquivos da aplicação Flask
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Instala as dependências Python
RUN pip install Flask docker Flask-Login Werkzeug

# Expõe a porta que o Flask vai usar
EXPOSE 5000
EXPOSE 7681

# Comando para iniciar a aplicação Flask
CMD ["python", "app.py"]