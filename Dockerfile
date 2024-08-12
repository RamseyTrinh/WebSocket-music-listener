FROM python:3.12.5-slim-bookworm

WORKDIR /app

RUN pip3 install pygments

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

RUN spotdl --download-ffmpeg

COPY . .

CMD ["python3", "app.py"]
