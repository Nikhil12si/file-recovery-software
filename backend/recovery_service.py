r"""
recovery_service.py - entry point of the Track B FastAPI service.

    # Windows, from an *elevated* terminal (raw disk handles need Administrator):
    python -m venv .venv && .venv\Scripts\activate
    pip install -r requirements.txt
    python recovery_service.py            # or: uvicorn recovery_service:app --port 8000

Environment variables are documented in app/config.py and .env.example.
"""
from app.main import app  # noqa: F401  (re-exported for `uvicorn recovery_service:app`)

if __name__ == "__main__":
    import os

    import uvicorn

    # Bind to loopback only: this service can read raw disks and must never be exposed on a network.
    uvicorn.run("recovery_service:app", host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")),
                log_level="info", reload=False)
