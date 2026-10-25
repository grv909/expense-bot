FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY msgparser.py bot.py ./
CMD ["python", "bot.py"]
