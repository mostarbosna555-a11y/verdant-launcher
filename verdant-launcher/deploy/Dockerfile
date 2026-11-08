FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.162.2 uvicorn==0.54.0 pydantic==2.14.3
COPY verdant/__init__.py verdant/models.py verdant/server.py /app/verdant/
RUN useradd --uid 10001 --create-home verdant && mkdir /data && chown verdant:verdant /data
USER verdant
ENV VERDANT_DB=/data/friends.sqlite3
EXPOSE 8787
CMD ["uvicorn", "verdant.server:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--no-proxy-headers", "--no-access-log"]
