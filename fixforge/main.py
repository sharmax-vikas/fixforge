from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import ApprovalRequest, CreateJobRequest, Job
from .services import JobStore

store = JobStore(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="FixForge", version="1.0.0", description="Approval-first AI GitHub issue resolution agent.", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="fixforge/static"), name="static")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse("fixforge/static/index.html")


@app.post("/api/jobs", response_model=Job, status_code=202)
def create_job(request: CreateJobRequest) -> Job:
    return store.create(str(request.issue_url))


@app.get("/api/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    try:
        return store.get(job_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/api/jobs/{job_id}/approvals", response_model=Job, status_code=202)
def approve(job_id: str, request: ApprovalRequest) -> Job:
    try:
        return store.approve(job_id, request.action, request.candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
