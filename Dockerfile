FROM python:3.12-slim

WORKDIR /app

COPY parser/ ./parser/
COPY frontend/ ./frontend/
COPY server/ ./server/

ENV CLAUDE_DIR=/data/.claude
ENV STATS_PATH=/data/stats.json
ENV REFRESH_INTERVAL=300
ENV PORT=9420

EXPOSE 9420

CMD ["python3", "server/serve.py"]
