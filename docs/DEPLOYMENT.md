# Deployment Notes

This project is designed for local production-style deployment without Docker.

## Backend

1. Install Python dependencies into `.venv`.
2. Keep `.env` at the project root.
3. Ensure Ollama is running on `http://localhost:11434`.
4. Ingest seed or uploaded files before expecting grounded answers.
5. Run:

```powershell
.\.venv\Scripts\python.exe scripts\run_backend.py
```

For a background service on Windows, run the same command through Task Scheduler or NSSM and set the working directory to the project root.

## Frontend

For development:

```powershell
cd frontend
npm.cmd run dev
```

For production static assets:

```powershell
cd frontend
npm.cmd run build
```

Serve `frontend/dist` from any static web server. Keep `VITE_API_BASE_URL` pointed at the backend.

## Operations Checklist

- Verify `GET /health` returns `ok`.
- Verify logs are written under `logs/app.log`.
- Verify Chroma data persists under `storage/chroma`.
- Verify BM25 index persists under `storage/bm25`.
- Keep uploaded files under `storage/uploads` backed up if they are important.
- Pin model names in `.env` before evaluation so results remain reproducible.

## Security Notes

Authentication is disabled by request. For shared networks, run behind a reverse proxy with authentication or bind `BACKEND_HOST=127.0.0.1`.
