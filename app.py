import os
import sys
import json
import asyncio
import requests
import uvicorn
import multiprocessing
from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
import tempfile

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

config_path = os.path.join(get_base_dir(), "config.json")

# 1. Load config.json ONLY if it exists locally (do not exit if missing on Vercel)
config = {}
if os.path.exists(config_path):
    with open(config_path, "r") as f:
        config = json.load(f)

# 2. Populate config from Vercel Environment Variables (with local fallback)
config["gemini_api_key"] = os.getenv("GEMINI_API_KEY") or config.get("gemini_api_key", "")
config["webhook_url"] = os.getenv("WEBHOOK_URL") or config.get("webhook_url", "")
config["default_location"] = os.getenv("DEFAULT_LOCATION") or config.get("default_location", "Warehouse")
config["rate_limit_delay_seconds"] = float(
    os.getenv("RATE_LIMIT_DELAY_SECONDS") or config.get("rate_limit_delay_seconds", 4.2)
)
config["port"] = int(os.getenv("PORT") or config.get("port", 8000))

# 3. Standalone variables (including PORT for local Uvicorn)
GEMINI_API_KEY = config["gemini_api_key"]
WEBHOOK_URL = config["webhook_url"]
DEFAULT_LOCATION = config["default_location"]
RATE_LIMIT_DELAY = config["rate_limit_delay_seconds"]
PORT = config["port"]

app = FastAPI()
client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------------------------------------------------
# Pydantic Data Model (Includes Col A: Bin Number)
# -------------------------------------------------------------
class InventoryItem(BaseModel):
    bin_number: str = Field(default="", description="Bin, box, pallet, or tote number/ID if mentioned, e.g., '12', 'A-3', 'Bin 4'")
    item_name: str = Field(description="Item name, e.g. Cereal, Misc, Snacks, Pasta, Beans, EMPTY")
    is_sorted: bool = Field(default=True, description="True if item is sorted, False if not sorted")
    location: str = Field(default="Warehouse", description="Location name, e.g., Warehouse, WOW, Friday's Truck, Serving 1, Kid's Truck")
    expiry_date: str = Field(description="Expiration date formatted as M/D/YY or M/D/YYYY, or 'None'")
    notes: str = Field(description="Variety notes, flavors, specific brands, or remarks")

@app.post("/extract-audio")
async def extract_audio(audio: UploadFile):
    audio_bytes = await audio.read()
    try:
        prompt = (
            f"Extract inventory donation item details from this audio including bin number if spoken. "
            f"Default location is '{DEFAULT_LOCATION}'."
        )
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, types.Part.from_bytes(data=audio_bytes, mime_type="audio/webm")],
            config={"response_mime_type": "application/json", "response_schema": InventoryItem}
        )
        return JSONResponse(content=json.loads(response.text))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/submit-row")
async def submit_row(
    mic_id: str = Form(...),
    bin_number: str = Form(""),
    item_name: str = Form(...),
    is_sorted: bool = Form(...),
    location: str = Form(...),
    expiry_date: str = Form(...),
    notes: str = Form(...)
):
    payload = {
        "mic_id": mic_id,
        "data": json.dumps({
            "bin_number": bin_number,
            "item_name": item_name,
            "is_sorted": is_sorted,
            "location": location,
            "expiry_date": expiry_date,
            "notes": notes
        })
    }
    requests.post(WEBHOOK_URL, json=payload)
    return {"status": "success"}

@app.get("/", response_class=HTMLResponse)
async def get_index():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <meta name="theme-color" content="#0066cc">
        <title>Inventory Scanner</title>
        <style>
            * { box-sizing: border-box; }
            body { 
                font-family: system-ui, -apple-system, sans-serif; 
                max-width: 450px; margin: 0 auto; padding: 15px; 
                background-color: #f8f9fa; color: #333;
            }
            .header { text-align: center; margin-bottom: 15px; }
            .station-badge { display: inline-block; background: #e9ecef; padding: 4px 12px; border-radius: 15px; font-weight: bold; font-size: 14px; color: #495057; }
            
            .voice-box { background: #e7f1ff; border: 2px dashed #0066cc; padding: 15px; border-radius: 12px; text-align: center; margin-bottom: 20px; }
            .record-btn { 
                padding: 15px 25px; font-size: 16px; font-weight: bold; background: #0066cc; 
                color: white; border: none; border-radius: 30px; cursor: pointer; 
                width: 100%; box-shadow: 0 4px 10px rgba(0,102,204,0.3); 
            }
            .record-btn.recording { background: #dc3545; }

            .form-group { margin-bottom: 12px; }
            label { display: block; font-weight: 600; font-size: 13px; margin-bottom: 4px; color: #555; }
            input[type="text"], select { width: 100%; padding: 10px; font-size: 16px; border: 1px solid #ced4da; border-radius: 8px; background: #fff; }
            .checkbox-group { display: flex; align-items: center; gap: 8px; margin-top: 5px; }
            .checkbox-group input { width: 20px; height: 20px; }

            .submit-btn { 
                width: 100%; padding: 14px; font-size: 18px; font-weight: bold; 
                background: #28a745; color: white; border: none; border-radius: 8px; 
                cursor: pointer; margin-top: 10px; box-shadow: 0 4px 10px rgba(40,167,69,0.3);
            }
            #status { font-size: 14px; font-weight: 600; text-align: center; margin-top: 10px; min-height: 20px; }
        </style>
    </head>
    <body>
        <div class="header">
            <h2 style="margin:0;">📦 Inventory Entry</h2>
            <div id="stationDisplay" class="station-badge">Station 1</div>
        </div>

        <div class="voice-box">
            <p style="margin:0 0 10px 0; font-size:13px; color:#004085;">Prefer speaking? Hold to auto-fill form:</p>
            <button id="recordBtn" class="record-btn">Hold to Speak</button>
        </div>

        <form id="inventoryForm">
            <div class="form-group">
                <label>Bin Number (Col A)</label>
                <input type="text" id="bin_number" placeholder="e.g., Bin 12, 104, A-3">
            </div>

            <div class="form-group">
                <label>Item Name (Col C)</label>
                <input type="text" id="item_name" required placeholder="e.g., Cereal, Beans, Snacks">
            </div>

            <div class="form-group">
                <label>Location (Col E)</label>
                <select id="location">
                    <option value="Warehouse">Warehouse</option>
                    <option value="WOW">WOW</option>
                    <option value="Truck">Truck</option>
                    <option value="Friday's Truck">Friday's Truck</option>
                    <option value="Serving 1">Serving 1</option>
                    <option value="Kid's Truck">Kid's Truck</option>
                </select>
            </div>

            <div class="form-group">
                <label>Expiration Date (Col F)</label>
                <input type="text" id="expiry_date" placeholder="e.g., 9/1/2026 or None">
            </div>

            <div class="form-group">
                <label>Notes / Variety (Col G)</label>
                <input type="text" id="notes" placeholder="e.g., chips, variety pack">
            </div>

            <div class="form-group">
                <div class="checkbox-group">
                    <input type="checkbox" id="is_sorted" checked>
                    <label for="is_sorted" style="margin:0;">Sorted Item (Col D)</label>
                </div>
            </div>

            <button type="submit" class="submit-btn">Save to Sheet</button>
        </form>

        <p id="status"></p>

        <script>
            const urlParams = new URLSearchParams(window.location.search);
            const currentStation = urlParams.get('station') || 'Station 1';
            document.getElementById('stationDisplay').innerText = currentStation;

            let mediaRecorder;
            let audioChunks = [];
            const recordBtn = document.getElementById('recordBtn');
            const statusText = document.getElementById('status');

            navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
                mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
                mediaRecorder.onstop = async () => {
                    statusText.innerText = "Processing audio...";
                    statusText.style.color = "#0066cc";
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const formData = new FormData();
                    formData.append("audio", audioBlob);

                    try {
                        const res = await fetch('/extract-audio', { method: 'POST', body: formData });
                        if (res.ok) {
                            const data = await res.json();
                            document.getElementById('bin_number').value = data.bin_number || '';
                            document.getElementById('item_name').value = data.item_name || '';
                            document.getElementById('location').value = data.location || 'Warehouse';
                            document.getElementById('expiry_date').value = data.expiry_date || '';
                            document.getElementById('notes').value = data.notes || '';
                            document.getElementById('is_sorted').checked = data.is_sorted ?? true;
                            statusText.innerText = "✓ Form auto-filled from speech!";
                            statusText.style.color = "#28a745";
                        } else {
                            statusText.innerText = "Error extracting audio.";
                            statusText.style.color = "#dc3545";
                        }
                    } catch (err) {
                        statusText.innerText = "Network error.";
                        statusText.style.color = "#dc3545";
                    }
                    audioChunks = [];
                };
            });

            function startRec(e) { e.preventDefault(); audioChunks = []; mediaRecorder.start(); recordBtn.classList.add('recording'); recordBtn.innerText = "Listening..."; }
            function stopRec(e) { e.preventDefault(); mediaRecorder.stop(); recordBtn.classList.remove('recording'); recordBtn.innerText = "Hold to Speak"; }

            recordBtn.addEventListener('touchstart', startRec);
            recordBtn.addEventListener('touchend', stopRec);
            recordBtn.addEventListener('mousedown', startRec);
            recordBtn.addEventListener('mouseup', stopRec);

            document.getElementById('inventoryForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                statusText.innerText = "Saving...";
                statusText.style.color = "#0066cc";

                const formData = new FormData();
                formData.append("mic_id", currentStation);
                formData.append("bin_number", document.getElementById('bin_number').value);
                formData.append("item_name", document.getElementById('item_name').value);
                formData.append("is_sorted", document.getElementById('is_sorted').checked);
                formData.append("location", document.getElementById('location').value);
                formData.append("expiry_date", document.getElementById('expiry_date').value);
                formData.append("notes", document.getElementById('notes').value);

                const res = await fetch('/submit-row', { method: 'POST', body: formData });
                if (res.ok) {
                    statusText.innerText = "✓ Successfully logged to Sheet!";
                    statusText.style.color = "#28a745";
                    document.getElementById('inventoryForm').reset();
                    document.getElementById('is_sorted').checked = true;
                } else {
                    statusText.innerText = "Error saving row.";
                    statusText.style.color = "#dc3545";
                }
            });
        </script>
    </body>
    </html>
    """

if __name__ == '__main__':
    multiprocessing.freeze_support()
    uvicorn.run(app, host="0.0.0.0", port=PORT)    