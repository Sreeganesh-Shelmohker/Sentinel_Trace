# Sentinel Trace

Mosswall is a dual-speed AI safety system providing sub-15ms inline prompt injection & jailbreak defense alongside continuous background hallucination and groundedness evaluation, powered by Moss.

## Monorepo Architecture

This repository is structured as a monorepo configured for independent two-tier deployment:

- **`backend/`**: FastAPI service powering semantic guardrails, groundedness evaluation, and SQLite audit tracing.
  - **Deployment**: [Render](https://render.com) (Web Service)
  - **Root Directory**: `backend`
  - **Build Command**: `pip install -r requirements.txt`
  - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

- **`frontend/`**: Next.js dashboard generated via v0 for real-time monitoring and chat.
  - **Deployment**: [Vercel](https://vercel.com)
  - **Root Directory**: `frontend`
  - **Framework Preset**: Next.js

## Local Development

### Backend
```bash
cd backend
cp .env.example .env
pip install -r requirements.txt
uvicorn main:app --reload
```
API runs on `http://localhost:8000`. Interactive OpenAPI documentation available at `http://localhost:8000/docs`.

### Frontend
```bash
cd frontend
npm install
npm run dev
```
