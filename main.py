from fastapi import FastAPI, HTTPException, BackgroundTasks, status
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
import glob
import json
import uuid
import asyncio
import aiofiles

from linkedin_scraper import LinkedInScraper

app = FastAPI(title="LinkedIn Scraper API", version="1.0.0")

# ------------------------------
# Storage Structure
# ------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
TASKS_DIR = os.path.join(DATA_DIR, "tasks")

print("--- Application starting ---")
print(f"BASE_DIR: {BASE_DIR}")
print(f"PROFILES_DIR: {PROFILES_DIR}")
print(f"TASKS_DIR: {TASKS_DIR}")

# Ensure directories exist upon startup
os.makedirs(PROFILES_DIR, exist_ok=True)
os.makedirs(TASKS_DIR, exist_ok=True)
print("Directories ensured.")


# ------------------------------
# Pydantic models
# ------------------------------
class ProfileBasic(BaseModel):
    id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    headline: Optional[str] = None

class ScrapeRequest(BaseModel):
    url: str
    cookie: str
    scrape_posts: bool = False
    scrape_comments: bool = False
    scrape_reactions: bool = False

# ------------------------------
# File-based Helper Functions
# ------------------------------
def _slug_from_url(url: str) -> str:
    return url.strip("/").split("/")[-1].split("?")[0]

async def _write_task_status(task_id: str, status_data: Dict[str, Any]):
    """Asynchronously write task status to a file."""
    task_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    try:
        async with aiofiles.open(task_file, "w", encoding="utf-8") as f:
            await f.write(json.dumps(status_data, indent=4))
    except Exception as e:
        print(f"[ERROR] Failed to write status for task {task_id}: {e}")


async def _read_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Asynchronously read task status from a file."""
    task_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    print(f"Checking for task file at: {task_file}")
    if not os.path.exists(task_file):
        print("File does not exist.")
        return None
    
    print("File exists, reading...")
    try:
        async with aiofiles.open(task_file, "r", encoding="utf-8") as f:
            content = await f.read()
            return json.loads(content)
    except Exception as e:
        print(f"[ERROR] Failed to read task file {task_file}: {e}")
        return None

# ------------------------------
# Main Scraper Task
# ------------------------------
async def _run_scraper_task(task_id: str, req: ScrapeRequest) -> None:
    profile_id = _slug_from_url(req.url)
    await _write_task_status(task_id, {"status": "running", "profile_id": profile_id})
    print(f"Task {task_id} status updated to 'running'")

    try:
        scraper = LinkedInScraper(req.cookie, req.url)
        scraped_data = await scraper.run(
            scrape_posts=req.scrape_posts,
            scrape_comments=req.scrape_comments,
            scrape_reactions=req.scrape_reactions,
        )
        await scraper.close_browser()

        if scraped_data:
            profile_file = os.path.join(PROFILES_DIR, f"{profile_id}.json")
            existing_data: Dict[str, Any] = {}
            if os.path.exists(profile_file):
                async with aiofiles.open(profile_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    existing_data = json.loads(content)
            
            existing_data.update(scraped_data)
            async with aiofiles.open(profile_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(existing_data, ensure_ascii=False, indent=4))
        
        await _write_task_status(task_id, {"status": "completed", "profile_id": profile_id})
        print(f"Task {task_id} status updated to 'completed'")

    except Exception as e:
        error_info = {"status": "error", "profile_id": profile_id, "error": str(e)}
        await _write_task_status(task_id, error_info)
        print(f"[CRITICAL] Task {task_id} failed with error: {e}")

# ------------------------------
# API Routes
# ------------------------------
@app.post("/profiles/scrape", status_code=status.HTTP_202_ACCEPTED)
async def scrape_profile(req: ScrapeRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    profile_id = _slug_from_url(req.url)
    
    # Use a synchronous write for the initial task creation to ensure it exists before returning.
    task_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    print(f"Creating task file (sync): {task_file}")
    try:
        with open(task_file, "w", encoding="utf-8") as f:
            json.dump({"status": "queued", "profile_id": profile_id}, f, indent=4)
        print(f"Successfully created task file for task_id: {task_id}")
    except Exception as e:
        print(f"[CRITICAL] Failed to create initial task file for {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create scraping task.")

    background_tasks.add_task(_run_scraper_task, task_id, req)
    return {"task_id": task_id, "profile_id": profile_id}


@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    print(f"Fetching status for task_id: {task_id}")
    task_data = await _read_task_status(task_id)
    if not task_data:
        print(f"Task file not found for task_id: {task_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    print(f"Found task data for {task_id}: {task_data.get('status')}")
    return task_data


@app.get("/profiles", response_model=List[ProfileBasic])
async def list_profiles() -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    profile_files = glob.glob(os.path.join(PROFILES_DIR, "*.json"))
    for file_path in profile_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            profile_id = os.path.basename(file_path).replace(".json", "")
            profiles.append({
                "id": profile_id,
                "first_name": data.get("first_name"),
                "last_name": data.get("last_name"),
                "headline": data.get("headline"),
            })
        except Exception as e:
            print(f"[WARN] Failed to load profile {file_path}: {e}")
    return profiles


@app.get("/profiles/search", response_model=List[ProfileBasic])
async def search_profiles(query: str) -> List[Dict[str, Any]]:
    query_lower = query.lower()
    results: List[Dict[str, Any]] = []
    profile_files = glob.glob(os.path.join(PROFILES_DIR, "*.json"))
    
    for file_path in profile_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                p = json.load(f)
            
            concat_fields = " ".join(filter(None, [p.get("first_name"), p.get("last_name"), p.get("headline")])).lower()
            if query_lower in concat_fields:
                profile_id = os.path.basename(file_path).replace(".json", "")
                results.append({
                    "id": profile_id,
                    "first_name": p.get("first_name"),
                    "last_name": p.get("last_name"),
                    "headline": p.get("headline"),
                })
        except Exception as e:
            print(f"[WARN] Failed to search profile {file_path}: {e}")
    return results


@app.get("/profiles/{profile_id}")
async def get_profile(profile_id: str) -> Dict[str, Any]:
    profile_file = os.path.join(PROFILES_DIR, f"{profile_id}.json")
    if not os.path.exists(profile_file):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    
    with open(profile_file, "r", encoding="utf-8") as f:
        return json.load(f)
